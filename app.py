# Para usar este script, instale todas as dependências:
# pip install Flask Flask-Cors opencv-python numpy imageio imageio-ffmpeg Pillow

from flask import Flask, request, send_from_directory, jsonify
from flask_cors import CORS
import cv2
import numpy as np
import imageio
from PIL import Image, ImageDraw, ImageFont
import os
import uuid
import threading
import time
import json
from datetime import datetime
import re

# --- Início da Lógica do Motor Gráfico (integrada ao servidor) ---

# Define os caminhos para os assets fixos.
ASSETS_FOLDER = 'assets'
FONT_PATH = os.path.join(ASSETS_FOLDER, 'Figtree-Bold.ttf')
IDENTITY_VIDEO_PATH = os.path.join(ASSETS_FOLDER, 'base_wfhd.webm')
FADE_IMAGE_PATH = os.path.join(ASSETS_FOLDER, 'fade_wfhd.png')
# Caminhos para arquivos na raiz do projeto
PREVIEW_FILE_PATH = 'preview.jpg'
SETTINGS_FILE_PATH = 'settings.json'


def draw_text_with_tracking(draw, pos, text, font, fill, tracking=0):
    """Desenha texto com espaçamento de letra (tracking) customizado."""
    x, y = pos
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        try: char_width = font.getbbox(char)[2]
        except AttributeError: char_width = font.getsize(char)[0]
        x += char_width + tracking

def wrap_text(text, font, max_width, tracking=0):
    """Quebra o texto em múltiplas linhas para caber em uma largura máxima, considerando o tracking."""
    lines = []
    words = text.split(' ')
    
    def get_line_width(line_text):
        width = 0
        if not line_text: return 0
        for char in line_text:
            try: width += font.getbbox(char)[2] + tracking
            except AttributeError: width += font.getsize(char)[0] + tracking
        if width > 0: width -= tracking
        return width

    i = 0
    while i < len(words):
        line = ''
        while i < len(words) and get_line_width(line + words[i]) <= max_width:
            line = line + words[i] + " "
            i += 1
        if not line:
            line = words[i]
            i += 1
        lines.append(line.strip())
    return lines

def processar_frame(frame_fundo_bgr, frame_identidade_bgr, img_fade, frame_count, fps, params):
    """Processa um único frame com a nova lógica de camadas."""
    frame_height, frame_width, _ = frame_fundo_bgr.shape

    if img_fade.shape[2] != 4:
         raise ValueError("A imagem fade_wfhd.png precisa ter um canal alfa.")
    
    if img_fade.shape[:2] != (frame_height, frame_width):
        img_fade = cv2.resize(img_fade, (frame_width, frame_height))

    fade_bgr = img_fade[:, :, 0:3]
    fade_alpha = img_fade[:, :, 3] / 255.0
    fade_alpha_3ch = cv2.merge([fade_alpha, fade_alpha, fade_alpha])

    multiplied_region = cv2.multiply(frame_fundo_bgr.astype(float), fade_bgr.astype(float), scale=1/255.0)
    camada_com_fade = multiplied_region * fade_alpha_3ch + frame_fundo_bgr.astype(float) * (1.0 - fade_alpha_3ch)
    camada_com_fade_uint8 = camada_com_fade.astype(np.uint8)

    pil_img = Image.fromarray(cv2.cvtColor(camada_com_fade_uint8, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    
    font_retranca = ImageFont.truetype(FONT_PATH, params['fontSizeRetranca'])
    font_titulo = ImageFont.truetype(FONT_PATH, params['fontSizeTitulo'])

    padding = 15
    try: _, _, retranca_w, retranca_h = draw.textbbox((0, 0), params['retranca'], font=font_retranca)
    except AttributeError: retranca_w, retranca_h = draw.textsize(params['retranca'], font=font_retranca)
    
    box_coords = [(params['posXRetranca'] - padding, params['posYRetranca'] - padding), 
                  (params['posXRetranca'] + retranca_w + padding, params['posYRetranca'] + retranca_h + padding)]
    draw.rectangle(box_coords, fill="white")
    draw.text((params['posXRetranca'], params['posYRetranca']), params['retranca'], font=font_retranca, fill="#3155A1")

    max_width = frame_width - params['posXTitulo'] - 50
    linhas_titulo = wrap_text(params['titulo'], font_titulo, max_width, tracking=params['letterSpacingTitulo'])
    y_text = params['posYTitulo']
    for linha in linhas_titulo:
        draw_text_with_tracking(draw, (params['posXTitulo'], y_text), linha, font_titulo, fill="white", tracking=params['letterSpacingTitulo'])
        try: y_text += font_titulo.getbbox("A")[3] + params['lineSpacingTitulo']
        except AttributeError: y_text += font_titulo.getsize("A")[1] + params['lineSpacingTitulo']

    frame_com_texto = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    if frame_identidade_bgr is not None:
        if frame_identidade_bgr.shape[:2] != (frame_height, frame_width):
            frame_identidade_bgr = cv2.resize(frame_identidade_bgr, (frame_width, frame_height))
        
        fade_start_frame, fade_end_frame = 3.0 * fps, 3.5 * fps
        fade_duration_frames = fade_end_frame - fade_start_frame
        fade_opacity = 1.0
        if frame_count >= fade_end_frame: fade_opacity = 0.0
        elif frame_count > fade_start_frame: fade_opacity = 1.0 - ((frame_count - fade_start_frame) / fade_duration_frames)
        
        frame_final = cv2.addWeighted(frame_identidade_bgr, fade_opacity, frame_com_texto, 1.0 - fade_opacity, 0)
    else:
        frame_final = frame_com_texto

    return frame_final

# --- Fim da Lógica do Motor Gráfico ---


# --- Início do Servidor Flask ---

app = Flask(__name__)
CORS(app) # Habilita o CORS para todas as rotas

UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(ASSETS_FOLDER, exist_ok=True)

def cleanup_files(paths):
    time.sleep(600)
    for path in paths:
        try: 
            os.remove(path)
            print(f"[LOG] Arquivo temporário removido: {path}")
        except OSError: pass

def get_form_params(form_data):
    return {
        'retranca': form_data.get('retranca', 'RETRANCA'),
        'titulo': form_data.get('titulo', 'Título de Exemplo'),
        'fontSizeRetranca': form_data.get('fontSizeRetranca', 40, type=int),
        'fontSizeTitulo': form_data.get('fontSizeTitulo', 85, type=int),
        'posXRetranca': form_data.get('posXRetranca', 1027, type=int),
        'posYRetranca': form_data.get('posYRetranca', 223, type=int),
        'posXTitulo': form_data.get('posXTitulo', 1000, type=int),
        'posYTitulo': form_data.get('posYTitulo', 280, type=int),
        'letterSpacingTitulo': form_data.get('letterSpacingTitulo', 0, type=int),
        'lineSpacingTitulo': form_data.get('lineSpacingTitulo', 4, type=int),
    }

def check_fixed_assets():
    """Verifica se os arquivos de assets fixos existem."""
    if not os.path.exists(FONT_PATH): raise FileNotFoundError(f"Fonte não encontrada em '{FONT_PATH}'")
    if not os.path.exists(IDENTITY_VIDEO_PATH): raise FileNotFoundError(f"Vídeo de identidade não encontrado em '{IDENTITY_VIDEO_PATH}'")
    if not os.path.exists(FADE_IMAGE_PATH): raise FileNotFoundError(f"Imagem de fade não encontrada em '{FADE_IMAGE_PATH}'")

def is_video(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ['mp4', 'webm', 'mov', 'avi']

@app.route('/upload-media', methods=['POST'])
def upload_media():
    print("\n[LOG] Recebida requisição para upload de mídia.")
    try:
        user_media_file = request.files.get('userMedia')
        if not user_media_file:
            return jsonify({'error': 'Nenhum ficheiro de mídia enviado.'}), 400

        # Limpa mídias de usuário antigas na raiz
        for item in os.listdir('.'):
            if item.startswith("user_media."):
                os.remove(item)
                print(f"[LOG] Mídia de usuário antiga removida: {item}")

        ext = user_media_file.filename.rsplit('.', 1)[1].lower()
        new_filename = f"user_media.{ext}"
        user_media_path = new_filename # Salva na raiz
        user_media_file.save(user_media_path)
        print(f"[LOG] Nova mídia de usuário salva em: {user_media_path}")

        # Salva o nome do novo ficheiro nas configurações
        settings = {}
        if os.path.exists(SETTINGS_FILE_PATH):
            with open(SETTINGS_FILE_PATH, 'r') as f:
                settings = json.load(f)
        
        settings['userMediaFilename'] = new_filename
        settings['userMediaOriginalFilename'] = user_media_file.filename
        
        with open(SETTINGS_FILE_PATH, 'w') as f:
            json.dump(settings, f, indent=4)
        print(f"[LOG] Nome da mídia salvo em 'settings.json'.")

        return jsonify({'status': 'success', 'filename': new_filename})
    except Exception as e:
        print(f"[ERRO] Falha no upload da mídia: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/generate-video', methods=['POST'])
def generate_video_endpoint():
    print("\n[LOG] Recebida requisição para gerar vídeo completo.")
    try:
        check_fixed_assets()
        
        with open(SETTINGS_FILE_PATH, 'r') as f:
            settings = json.load(f)
        
        user_media_filename = settings.get('userMediaFilename')
        if not user_media_filename or not os.path.exists(user_media_filename):
            raise FileNotFoundError("Nenhuma mídia de fundo foi enviada para o servidor ainda.")
        
        print(f"[LOG] Usando mídia de fundo: {user_media_filename}")
        params = get_form_params(request.form)

        # Geração do nome do arquivo final
        date_str = datetime.now().strftime("%d%m%Y")
        retranca_str = re.sub(r'[^a-zA-Z0-9_]', '', params['retranca'].replace(' ', '_')).upper()
        output_filename = f"{date_str}_WIDEFULLHD_URBNEWS_{retranca_str}.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        identity_video_reader = imageio.get_reader(IDENTITY_VIDEO_PATH)
        img_fade = cv2.imread(FADE_IMAGE_PATH, cv2.IMREAD_UNCHANGED)
        
        meta_identidade = identity_video_reader.get_meta_data()
        fps = meta_identidade.get('fps', 30)
        frame_width, frame_height = int(meta_identidade['size'][0]), int(meta_identidade['size'][1])

        saida = cv2.VideoWriter(output_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, (frame_width, frame_height))
        
        print(f"[LOG] Iniciando renderização. FPS: {fps}, Resolução: {frame_width}x{frame_height}")
        
        if is_video(user_media_filename):
            user_media_reader = imageio.get_reader(user_media_filename)
            for i, (frame_identidade_rgb, frame_usuario_rgb) in enumerate(zip(identity_video_reader, user_media_reader)):
                frame_identidade_bgr = cv2.cvtColor(frame_identidade_rgb, cv2.COLOR_RGB2BGR)
                frame_usuario_bgr = cv2.cvtColor(frame_usuario_rgb, cv2.COLOR_RGB2BGR)
                final_frame = processar_frame(frame_usuario_bgr, frame_identidade_bgr, img_fade, i, fps, params)
                saida.write(final_frame)
            user_media_reader.close()
        else: # Se for imagem
            img_usuario_bgr = cv2.imread(user_media_filename)
            if img_usuario_bgr is None: raise ValueError("Não foi possível ler a imagem do usuário.")
            img_usuario_bgr = cv2.resize(img_usuario_bgr, (frame_width, frame_height))
            for i, frame_identidade_rgb in enumerate(identity_video_reader):
                frame_identidade_bgr = cv2.cvtColor(frame_identidade_rgb, cv2.COLOR_RGB2BGR)
                final_frame = processar_frame(img_usuario_bgr, frame_identidade_bgr, img_fade, i, fps, params)
                saida.write(final_frame)

        identity_video_reader.close()
        saida.release()
        print(f"[LOG] Renderização concluída. Vídeo salvo em: {output_path}")

        threading.Thread(target=cleanup_files, args=([output_path],)).start()
        return jsonify({'downloadUrl': f'/output/{output_filename}'})

    except Exception as e:
        print(f"[ERRO] Falha na geração do vídeo: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/preview-frame', methods=['POST'])
def preview_frame_endpoint():
    print("\n[LOG] Recebida requisição de preview.")
    try:
        check_fixed_assets()
        
        with open(SETTINGS_FILE_PATH, 'r') as f:
            settings = json.load(f)
        
        user_media_filename = settings.get('userMediaFilename')
        if not user_media_filename or not os.path.exists(user_media_filename):
            raise FileNotFoundError("Nenhuma mídia de fundo foi enviada para o servidor ainda.")

        print(f"[LOG] Gerando preview para: {user_media_filename}")
        params = get_form_params(request.form)

        fps = 30
        preview_frame_index = int(5 * fps)
        
        frame_identidade_bgr = None
        img_fade = cv2.imread(FADE_IMAGE_PATH, cv2.IMREAD_UNCHANGED)
        
        if is_video(user_media_filename):
            user_media_reader = imageio.get_reader(user_media_filename)
            try: frame_usuario_rgb = user_media_reader.get_data(preview_frame_index)
            except IndexError: return jsonify({'error': f'O vídeo do usuário precisa ter pelo menos 5 segundos.'}), 400
            finally: user_media_reader.close()
            frame_usuario_bgr = cv2.cvtColor(frame_usuario_rgb, cv2.COLOR_RGB2BGR)
        else:
            frame_usuario_bgr = cv2.imread(user_media_filename)
            if frame_usuario_bgr is None: raise ValueError("Não foi possível ler a imagem do usuário.")

        final_frame = processar_frame(frame_usuario_bgr, frame_identidade_bgr, img_fade, preview_frame_index, fps, params)

        cv2.imwrite(PREVIEW_FILE_PATH, final_frame)
        
        print(f"[LOG] Preview gerado e salvo em: {PREVIEW_FILE_PATH}")
        return jsonify({'previewUrl': '/preview.jpg'})

    except Exception as e:
        print(f"[ERRO] Falha na geração do preview: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/load-settings', methods=['GET'])
def load_settings():
    print("\n[LOG] Recebida requisição para carregar configurações.")
    try:
        if not os.path.exists(SETTINGS_FILE_PATH):
            print("[LOG] Arquivo de configurações não encontrado. Criando um padrão.")
            default_settings = get_form_params({})
            with open(SETTINGS_FILE_PATH, 'w') as f:
                json.dump(default_settings, f, indent=4)
            return jsonify(default_settings)
        else:
            print("[LOG] Carregando configurações de 'settings.json'.")
            with open(SETTINGS_FILE_PATH, 'r') as f:
                settings = json.load(f)
            return jsonify(settings)
    except Exception as e:
        print(f"[ERRO] Falha ao carregar configurações: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/save-settings', methods=['POST'])
def save_settings():
    print("\n[LOG] Recebida requisição para salvar configurações.")
    try:
        new_settings = request.json
        
        current_settings = {}
        if os.path.exists(SETTINGS_FILE_PATH):
            with open(SETTINGS_FILE_PATH, 'r') as f:
                current_settings = json.load(f)
        
        current_settings.update(new_settings)

        with open(SETTINGS_FILE_PATH, 'w') as f:
            json.dump(current_settings, f, indent=4)
        print("[LOG] Configurações salvas com sucesso em 'settings.json'.")
        return jsonify({'status': 'success'})
    except Exception as e:
        print(f"[ERRO] Falha ao salvar configurações: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/output/<filename>')
def get_output_file(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)

@app.route('/assets/<filename>')
def get_asset_file(filename):
    return send_from_directory(ASSETS_FOLDER, filename)

@app.route('/preview.jpg')
def get_preview_from_root():
    return send_from_directory('.', 'preview.jpg')


if __name__ == '__main__':
    print("Servidor Flask iniciado. Acesse o arquivo index.html no seu navegador.")
    app.run(debug=True)

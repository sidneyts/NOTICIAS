# Para usar este script, instale todas as dependências:
# pip install Flask opencv-python numpy imageio imageio-ffmpeg Pillow

from flask import Flask, request, send_from_directory, jsonify, render_template
import cv2
import numpy as np
import imageio
from PIL import Image, ImageDraw, ImageFont
import os
import threading
import time
import json
from datetime import datetime
import re

app = Flask(__name__, template_folder='templates', static_folder='static')

# --- Configuração de Pastas ---
ASSETS_FOLDER = 'assets'
UPLOAD_FOLDER = 'uploads'
OUTPUT_FOLDER = 'output'
FONT_PATH = os.path.join(ASSETS_FOLDER, 'Figtree-Bold.ttf')
SETTINGS_FILE_PATH = 'settings.json'
PREVIEW_FILE_PATH = os.path.join('static', 'preview.jpg')

# Garante que as pastas necessárias existem
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(ASSETS_FOLDER, exist_ok=True)
os.makedirs('static', exist_ok=True)
os.makedirs('templates', exist_ok=True)


# --- Mapeamento de Formatos e Assets ---
FORMAT_ASSETS = {
    "1920x1080": {
        "video": os.path.join(ASSETS_FOLDER, 'base_16x9.webm'),
        "fade": os.path.join(ASSETS_FOLDER, 'fade_16x9.png'),
        "label": "WIDE"
    },
    "1080x1920": {
        "video": os.path.join(ASSETS_FOLDER, 'base_9x16.webm'),
        "fade": os.path.join(ASSETS_FOLDER, 'fade_9x16.png'),
        "label": "VERTICAL"
    },
    "2048x720": {
        "video": os.path.join(ASSETS_FOLDER, 'base_cinema.webm'),
        "fade": os.path.join(ASSETS_FOLDER, 'fade_cinema.png'),
        "label": "CINEMA"
    }
}
LOGO_IMAGE_PATH = os.path.join(ASSETS_FOLDER, 'logo_urbnews.png')


# --- Funções do Motor Gráfico ---
def overlay_image(background, overlay, x, y, scale):
    if scale <= 0: return background
    h_overlay, w_overlay, _ = overlay.shape
    w_scaled, h_scaled = int(w_overlay * scale), int(h_overlay * scale)
    if w_scaled <= 0 or h_scaled <= 0: return background
    overlay_resized = cv2.resize(overlay, (w_scaled, h_scaled), interpolation=cv2.INTER_AREA)
    h_bg, w_bg, _ = background.shape
    overlay_bgr, overlay_alpha = overlay_resized[:, :, 0:3], overlay_resized[:, :, 3] / 255.0
    x1_dest, y1_dest = max(0, int(x)), max(0, int(y))
    x2_dest, y2_dest = min(w_bg, int(x) + w_scaled), min(h_bg, int(y) + h_scaled)
    x1_src, y1_src = max(0, -int(x)), max(0, -int(y))
    if (x2_dest - x1_dest) > 0 and (y2_dest - y1_dest) > 0:
        dest_w, dest_h = x2_dest - x1_dest, y2_dest - y1_dest
        x2_src, y2_src = x1_src + dest_w, y1_src + dest_h
        roi = background[y1_dest:y2_dest, x1_dest:x2_dest]
        alpha_mask = cv2.merge([overlay_alpha[y1_src:y2_src, x1_src:x2_src]] * 3)
        bgr_src = overlay_bgr[y1_src:y2_src, x1_src:x2_src]
        composite = (bgr_src * alpha_mask) + (roi * (1.0 - alpha_mask))
        background[y1_dest:y2_dest, x1_dest:x2_dest] = composite
    return background

def draw_text_with_tracking(draw, pos, text, font, fill, tracking=0):
    x, y = pos
    for char in text:
        draw.text((x, y), char, font=font, fill=fill)
        try: char_width = font.getbbox(char)[2]
        except AttributeError: char_width = font.getsize(char)[0]
        x += char_width + tracking

def wrap_text(text, font, max_width, tracking=0):
    lines, words = [], text.split(' ')
    def get_line_width(line_text):
        width = 0
        if not line_text: return 0
        for char in line_text:
            try: width += font.getbbox(char)[2] + tracking
            except AttributeError: width += font.getsize(char)[0] + tracking
        return width - tracking if width > 0 else 0
    i = 0
    while i < len(words):
        line = ''
        while i < len(words) and get_line_width(line + words[i]) <= max_width:
            line += words[i] + " "
            i += 1
        if not line: line = words[i] + " "; i += 1
        lines.append(line.strip())
    return lines

def processar_frame(frame_fundo_bgr_original, img_logo, frame_identidade_bgr, img_fade, frame_count, fps, params, final_dimensions):
    frame_width, frame_height = final_dimensions
    bg_fill = cv2.resize(frame_fundo_bgr_original, (frame_width, frame_height), interpolation=cv2.INTER_LINEAR)
    blur_amount = int(params.get('blurFundo', 25))
    if blur_amount < 1: blur_amount = 1
    if blur_amount % 2 == 0: blur_amount += 1
    canvas = cv2.GaussianBlur(bg_fill, (blur_amount, blur_amount), 0)
    escala_fundo = params.get('escalaFundo', 1.0)
    h_orig, w_orig, _ = frame_fundo_bgr_original.shape
    w_scaled, h_scaled = int(w_orig * escala_fundo), int(h_orig * escala_fundo)
    if w_scaled > 0 and h_scaled > 0:
        scaled_fg = cv2.resize(frame_fundo_bgr_original, (w_scaled, h_scaled), interpolation=cv2.INTER_AREA)
        offset_x_fundo, offset_y_fundo = params.get('posXFundo', 0), params.get('posYFundo', 0)
        pos_x_fundo = int((frame_width - w_scaled) / 2) + offset_x_fundo
        pos_y_fundo = int((frame_height - h_scaled) / 2) + offset_y_fundo
        if scaled_fg.shape[2] == 3:
            scaled_fg = cv2.cvtColor(scaled_fg, cv2.COLOR_BGR2BGRA)
            scaled_fg[:, :, 3] = 255
        canvas = overlay_image(canvas, scaled_fg, pos_x_fundo, pos_y_fundo, 1.0)
    if img_fade.shape[2] != 4: raise ValueError("Imagem de fade precisa de canal alfa.")
    if img_fade.shape[:2] != (frame_height, frame_width): img_fade = cv2.resize(img_fade, (frame_width, frame_height))
    fade_bgr, fade_alpha_3ch = img_fade[:, :, 0:3], cv2.merge([img_fade[:, :, 3] / 255.0] * 3)
    imagem_com_fade = (cv2.multiply(canvas.astype(float), fade_bgr.astype(float), scale=1/255.0) * fade_alpha_3ch + canvas.astype(float) * (1.0 - fade_alpha_3ch)).astype(np.uint8)
    if img_logo is not None: imagem_com_fade = overlay_image(imagem_com_fade, img_logo, params.get('posXLogo', 0), params.get('posYLogo', 0), params.get('escalaLogo', 1.0))
    pil_img = Image.fromarray(cv2.cvtColor(imagem_com_fade, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font_retranca = ImageFont.truetype(FONT_PATH, int(params['fontSizeRetranca']))
    font_titulo = ImageFont.truetype(FONT_PATH, int(params['fontSizeTitulo']))
    padding = 15
    try: _, _, retranca_w, retranca_h = draw.textbbox((0, 0), params['retranca'], font=font_retranca)
    except AttributeError: retranca_w, retranca_h = draw.textsize(params['retranca'], font=font_retranca)
    box_coords = [(params['posXRetranca'] - padding, params['posYRetranca'] - padding), (params['posXRetranca'] + retranca_w + padding, params['posYRetranca'] + retranca_h + padding)]
    draw.rectangle(box_coords, fill="white")
    draw.text((params['posXRetranca'], params['posYRetranca']), params['retranca'], font=font_retranca, fill="#3155A1")
    max_width = frame_width - int(params['posXTitulo']) - 50
    linhas_titulo = wrap_text(params['titulo'], font_titulo, max_width, tracking=int(params['letterSpacingTitulo']))
    y_text = params['posYTitulo']
    for linha in linhas_titulo:
        draw_text_with_tracking(draw, (params['posXTitulo'], y_text), linha, font_titulo, fill="white", tracking=int(params['letterSpacingTitulo']))
        try: y_text += font_titulo.getbbox("A")[3] + params['lineSpacingTitulo']
        except AttributeError: y_text += font_titulo.getsize("A")[1] + params['lineSpacingTitulo']
    frame_com_texto = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    if frame_identidade_bgr is not None:
        if frame_identidade_bgr.shape[:2] != (frame_height, frame_width): frame_identidade_bgr = cv2.resize(frame_identidade_bgr, (frame_width, frame_width))
        fade_start, fade_end = 3.0 * fps, 3.5 * fps
        fade_duration = fade_end - fade_start
        opacity = 1.0
        if frame_count >= fade_end: opacity = 0.0
        elif frame_count > fade_start: opacity = 1.0 - ((frame_count - fade_start) / fade_duration)
        return cv2.addWeighted(frame_identidade_bgr, opacity, frame_com_texto, 1.0 - opacity, 0)
    return frame_com_texto


def render_video_for_format(format_key, global_params, format_params, user_media_filename):
    """Função dedicada para renderizar um vídeo para um formato específico."""
    print(f"\n[LOG] Iniciando renderização para o formato: {format_key}")
    
    assets = FORMAT_ASSETS[format_key]
    identity_video_path = assets["video"]
    fade_image_path = assets["fade"]
    
    if not all(os.path.exists(p) for p in [identity_video_path, fade_image_path, LOGO_IMAGE_PATH, FONT_PATH]):
        raise FileNotFoundError(f"Um ou mais ficheiros de assets não foram encontrados para o formato {format_key}.")

    params = {**global_params, **format_params}
    framerate = global_params.get('framerate', 30)

    date_str = datetime.now().strftime("%d%m%Y")
    retranca_str = re.sub(r'[^a-zA-Z0-9_]', '', params['retranca'].replace(' ', '_')).upper()
    format_label = assets["label"]
    output_filename = f"{date_str}_{format_label}_URBNEWS_{retranca_str}.mp4"
    output_path = os.path.join(OUTPUT_FOLDER, output_filename)

    identity_video_reader = imageio.get_reader(identity_video_path)
    img_fade = cv2.imread(fade_image_path, cv2.IMREAD_UNCHANGED)
    img_logo = cv2.imread(LOGO_IMAGE_PATH, cv2.IMREAD_UNCHANGED)

    w, h = [int(x) for x in format_key.split('x')]
    final_dimensions = (w, h)

    saida = cv2.VideoWriter(output_path, cv2.VideoWriter_fourcc(*'mp4v'), framerate, final_dimensions)

    is_video_media = '.' in user_media_filename and user_media_filename.rsplit('.', 1)[1].lower() in ['mp4', 'webm', 'mov']
    if is_video_media:
        user_media_reader = imageio.get_reader(user_media_filename)
        for i, (id_rgb, user_rgb) in enumerate(zip(identity_video_reader, user_media_reader)):
            id_bgr = cv2.cvtColor(id_rgb, cv2.COLOR_RGB2BGR)
            user_bgr = cv2.cvtColor(user_rgb, cv2.COLOR_RGB2BGR)
            final_frame = processar_frame(user_bgr, img_logo, id_bgr, img_fade, i, framerate, params, final_dimensions)
            saida.write(final_frame)
        user_media_reader.close()
    else:
        user_bgr = cv2.imread(user_media_filename)
        for i, id_rgb in enumerate(identity_video_reader):
            id_bgr = cv2.cvtColor(id_rgb, cv2.COLOR_RGB2BGR)
            final_frame = processar_frame(user_bgr, img_logo, id_bgr, img_fade, i, framerate, params, final_dimensions)
            saida.write(final_frame)

    identity_video_reader.close()
    saida.release()
    print(f"[LOG] Renderização para {format_key} concluída. Vídeo salvo em: {output_path}")
    return {"url": f'/output/{output_filename}', "label": f"{assets['label']} ({format_key})"}

# --- Rotas da API Flask ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/generate-video', methods=['POST'])
def generate_video_batch_endpoint():
    print("\n[LOG] Recebida requisição para gerar vídeos em LOTE.")
    try:
        with open(SETTINGS_FILE_PATH, 'r') as f:
            settings = json.load(f)
        
        user_media_filename = settings.get('userMediaFilename')
        if not user_media_filename or not os.path.exists(user_media_filename):
            raise FileNotFoundError("Nenhuma mídia de fundo foi enviada para o servidor.")

        global_params = {
            'retranca': settings.get('retranca', ''),
            'titulo': settings.get('titulo', ''),
            'framerate': settings.get('framerate', 30)
        }
        
        download_urls = []
        configured_formats = settings.get('formats', {})

        for format_key, format_params in configured_formats.items():
            if format_key in FORMAT_ASSETS:
                try:
                    result = render_video_for_format(format_key, global_params, format_params, user_media_filename)
                    download_urls.append(result)
                except Exception as e:
                    print(f"[ERRO] Falha ao renderizar o formato {format_key}: {e}")
                    download_urls.append({"url": "#", "label": f"Falha no formato {format_key}", "error": True})
            else:
                print(f"[AVISO] Formato '{format_key}' encontrado nas configurações mas não mapeado. Ignorando.")

        return jsonify({'downloadUrls': download_urls})

    except Exception as e:
        print(f"[ERRO GERAL] Geração de vídeo em lote: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/preview-frame', methods=['POST'])
def preview_frame_endpoint():
    print("\n[LOG] Recebida requisição de preview.")
    try:
        format_key = request.form.get('format')
        if not format_key or format_key not in FORMAT_ASSETS:
            return jsonify({'error': 'Formato inválido ou não especificado.'}), 400

        with open(SETTINGS_FILE_PATH, 'r') as f: settings = json.load(f)
        user_media_filename = settings.get('userMediaFilename')
        if not user_media_filename or not os.path.exists(user_media_filename):
            raise FileNotFoundError("Mídia de fundo não encontrada no servidor.")
        
        # --- LÓGICA DE CONVERSÃO CORRIGIDA ---
        params = {}
        for key, value in request.form.items():
            try:
                # Tenta converter o valor para float. Isto lida com inteiros, decimais e negativos.
                params[key] = float(value)
            except (ValueError, TypeError):
                # Se a conversão falhar, mantém o valor como string (ex: 'retranca', 'titulo').
                params[key] = value
        
        assets = FORMAT_ASSETS[format_key]
        w, h = [int(x) for x in format_key.split('x')]
        final_dimensions = (w, h)
        fps = int(params.get('framerate', 30))
        preview_frame_index = int(5 * fps)
        
        img_fade = cv2.imread(assets['fade'], cv2.IMREAD_UNCHANGED)
        img_logo = cv2.imread(LOGO_IMAGE_PATH, cv2.IMREAD_UNCHANGED)

        user_bgr = cv2.imread(user_media_filename)
        
        final_frame = processar_frame(user_bgr, img_logo, None, img_fade, preview_frame_index, fps, params, final_dimensions)
        cv2.imwrite(PREVIEW_FILE_PATH, final_frame)
        
        return jsonify({'previewUrl': '/static/preview.jpg'})

    except Exception as e:
        print(f"[ERRO] Geração de preview: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/upload-media', methods=['POST'])
def upload_media():
    if 'userMedia' not in request.files: return jsonify({'error': 'Nenhum ficheiro enviado.'}), 400
    file = request.files['userMedia']
    if file.filename == '': return jsonify({'error': 'Ficheiro sem nome selecionado.'}), 400
    
    for item in os.listdir('.'):
        if item.startswith("user_media."): os.remove(item)

    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"user_media.{ext}"
    file.save(filename)

    with open(SETTINGS_FILE_PATH, 'r+') as f:
        settings = json.load(f)
        settings['userMediaFilename'] = filename
        settings['userMediaOriginalFilename'] = file.filename
        f.seek(0); json.dump(settings, f, indent=4); f.truncate()

    return jsonify({'status': 'success', 'filename': filename})

@app.route('/load-settings', methods=['GET'])
def load_settings():
    if not os.path.exists(SETTINGS_FILE_PATH):
        default_settings = {
            "selectedFormat": "1920x1080", "retranca": "Retranca", "titulo": "Título", "framerate": 30,
            "formats": { "1920x1080": {}, "1080x1920": {}, "2048x720": {} }
        }
        with open(SETTINGS_FILE_PATH, 'w') as f: json.dump(default_settings, f, indent=4)
        return jsonify(default_settings)
    
    with open(SETTINGS_FILE_PATH, 'r') as f:
        settings = json.load(f)
    
    if "formats" not in settings:
        print("[LOG] Migrando 'settings.json' antigo.")
        old_settings = settings.copy()
        settings = {
            "selectedFormat": "1920x1080", "framerate": 30,
            "retranca": old_settings.pop("retranca", "Retranca"), "titulo": old_settings.pop("titulo", "Título"),
            "formats": {"1920x1080": old_settings, "1080x1920": {}, "2048x720": {}}
        }
    if "framerate" not in settings: settings["framerate"] = 30
    
    with open(SETTINGS_FILE_PATH, 'w') as f: json.dump(settings, f, indent=4)
    return jsonify(settings)

@app.route('/save-settings', methods=['POST'])
def save_settings():
    try:
        new_settings = request.json
        with open(SETTINGS_FILE_PATH, 'w') as f:
            json.dump(new_settings, f, indent=4)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/output/<filename>')
def get_output_file(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)


if __name__ == '__main__':
    print("Servidor Flask iniciado. Acesse http://127.0.0.1:5000 no seu navegador.")
    app.run(debug=True, port=5000)


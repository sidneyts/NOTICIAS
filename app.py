# Para usar este script, instale todas as dependências:
# pip install Flask opencv-python numpy imageio imageio-ffmpeg Pillow

from flask import Flask, request, send_from_directory, jsonify, render_template
import cv2
import numpy as np
import imageio
from PIL import Image, ImageDraw, ImageFont
import os
import threading
import json
from datetime import datetime
import re
import zipfile
import shutil

# --- Configurações Globais ---
preview_lock = threading.Lock()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_FOLDER = os.path.join(BASE_DIR, 'assets')
STATIC_FOLDER = os.path.join(BASE_DIR, 'static')
TEMPLATES_FOLDER = os.path.join(BASE_DIR, 'templates')
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
OUTPUT_FOLDER = os.path.join(BASE_DIR, 'output')

PREVIEW_FILE_PATH = os.path.join(STATIC_FOLDER, 'preview.jpg')
SETTINGS_FILE_PATH = os.path.join(BASE_DIR, 'settings.json')

# Mapeamento de formatos para os seus respetivos ficheiros de assets
FORMAT_ASSETS = {
    "1920x1080": {"label": "WIDEFULLHD", "base": "base_16x9.webm", "fade": "fade_16x9.png"},
    "1080x1920": {"label": "VERTFULLHD", "base": "base_9x16.webm", "fade": "fade_9x16.png"},
    "2048x720": {"label": "MUB-FOR-SP", "base": "base_cinema.webm", "fade": "fade_cinema.png"},
    "800x600": {"label": "BOX", "base": "base_box.webm", "fade": "fade_box.png"},
    "960x1344": {"label": "TOTEMG", "base": "base_abrigo.webm", "fade": "fade_abrigo.png"}
}

# --- Motor Gráfico ---

def overlay_image(background, overlay, x, y, scale):
    if scale <= 0: return background
    h_overlay, w_overlay, _ = overlay.shape
    w_scaled, h_scaled = int(w_overlay * scale), int(h_overlay * scale)
    if w_scaled <= 0 or h_scaled <= 0: return background
    
    overlay_resized = cv2.resize(overlay, (w_scaled, h_scaled), interpolation=cv2.INTER_AREA)

    h_bg, w_bg, _ = background.shape
    overlay_bgr, overlay_alpha = overlay_resized[:, :, 0:3], overlay_resized[:, :, 3] / 255.0
    
    x, y = int(x), int(y)

    x1_dest, y1_dest = max(0, x), max(0, y)
    x2_dest, y2_dest = min(w_bg, x + w_scaled), min(h_bg, y + h_scaled)
    x1_src, y1_src = max(0, -x), max(0, -y)
    
    if (x2_dest - x1_dest) > 0 and (y2_dest - y1_dest) > 0:
        dest_w, dest_h = x2_dest - x1_dest, y2_dest - y1_dest
        x2_src, y2_src = x1_src + dest_w, y1_src + dest_h
        
        y1_dest, y2_dest, x1_dest, x2_dest = int(y1_dest), int(y2_dest), int(x1_dest), int(x2_dest)
        y1_src, y2_src, x1_src, x2_src = int(y1_src), int(y2_src), int(x1_src), int(x2_src)

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
        char_width = font.getbbox(char)[2] if hasattr(font, 'getbbox') else font.getsize(char)[0]
        x += char_width + tracking

def wrap_text(text, font, max_width, tracking=0):
    lines, words = [], text.split(' ')
    def get_line_width(line_text):
        if not line_text: return 0
        width = sum(font.getbbox(char)[2] + tracking for char in line_text) if hasattr(font, 'getbbox') else sum(font.getsize(char)[0] + tracking for char in line_text)
        return width - tracking if width > 0 else 0
    i = 0
    while i < len(words):
        line = ''
        while i < len(words) and get_line_width(line + words[i]) <= max_width:
            line += words[i] + " "
            i += 1
        if not line: line = words[i]; i += 1
        lines.append(line.strip())
    return lines

# --- NOVA FUNÇÃO PARA CRIAR A MÁSCARA DE BORDA ---
def create_edge_fade_mask(width, height, rotation, position_offset, fade_intensity):
    # Cria uma tela grande para evitar cortes na rotação
    diag = int(np.sqrt(width**2 + height**2))
    mask = np.zeros((diag, diag), dtype=np.uint8)
    
    # A intensidade (0 a 1) controla a largura do fade. Multiplicamos por 2 para um efeito mais visível.
    fade_width = int(width * fade_intensity * 2)
    if fade_width < 1: # Se a intensidade for 0, a máscara será totalmente opaca
        mask.fill(255)
    else:
        # A posição (offset) move a "linha imaginária" (o centro do fade)
        fade_center = (diag // 2) + position_offset
        opaque_end = fade_center - (fade_width // 2)
        
        # --- CORREÇÃO DO ERRO ---
        # Garante que os índices de slice sejam inteiros
        opaque_end_int = int(opaque_end)
        
        # Preenche a parte totalmente opaca (branca)
        mask[:, :max(0, opaque_end_int)] = 255
        
        # Cria o gradiente (branco para preto)
        gradient_zone = np.linspace(255, 0, fade_width, dtype=np.uint8)
        for i in range(fade_width):
            col_index = opaque_end_int + i
            if 0 <= col_index < diag:
                mask[:, col_index] = gradient_zone[i]
            
    # Rotaciona a máscara
    center = (diag // 2, diag // 2)
    rot_mat = cv2.getRotationMatrix2D(center, rotation, 1.0)
    rotated_mask = cv2.warpAffine(mask, rot_mat, (diag, diag), flags=cv2.INTER_LINEAR)
    
    # Corta o centro da máscara rotacionada para o tamanho da imagem original
    crop_x = (diag - width) // 2
    crop_y = (diag - height) // 2
    final_mask = rotated_mask[crop_y:crop_y + height, crop_x:crop_x + width]
    
    return final_mask

def processar_frame(frame_fundo_bgr_original, img_logo, frame_identidade_bgr, img_fade, frame_count, fps, params, final_dimensions, format_key):
    frame_width, frame_height = final_dimensions
    
    # Camada 1: Fundo Desfocado
    bg_fill = cv2.resize(frame_fundo_bgr_original, (frame_width, frame_height), interpolation=cv2.INTER_LINEAR)
    raw_blur = params.get('blurFundo', 25)
    blur_amount = max(1, int(raw_blur))
    if blur_amount % 2 == 0: blur_amount += 1
    canvas = cv2.GaussianBlur(bg_fill, (blur_amount, blur_amount), 0)

    # Camada 2: Mídia Principal
    escala_fundo = params.get('escalaFundo', 1.0)
    h_orig, w_orig, _ = frame_fundo_bgr_original.shape
    w_scaled, h_scaled = int(w_orig * escala_fundo), int(h_orig * escala_fundo)
    if w_scaled > 0 and h_scaled > 0:
        scaled_fg = cv2.resize(frame_fundo_bgr_original, (w_scaled, h_scaled), interpolation=cv2.INTER_AREA)
        
        # Garante que a mídia principal tenha 4 canais (BGRA)
        if scaled_fg.shape[2] == 3:
            scaled_fg_rgba = cv2.cvtColor(scaled_fg, cv2.COLOR_BGR2BGRA)
        else:
            scaled_fg_rgba = scaled_fg

        # --- LÓGICA DE MÁSCARA DE BORDA ATUALIZADA ---
        intensidade_mascara = params.get('intensidadeMascara', 0.0)
        # A máscara só é criada se a intensidade for maior que zero
        if intensidade_mascara > 0:
            rotacao = params.get('rotacaoMascara', 0.0)
            # O slider de posição Y não é usado aqui, apenas o X
            posicao = params.get('posXMascara', 0) 
            
            edge_mask = create_edge_fade_mask(w_scaled, h_scaled, rotacao, posicao, intensidade_mascara)
            
            b, g, r, a = cv2.split(scaled_fg_rgba)
            # A nova máscara é mesclada com o canal alfa existente
            # (útil se a imagem original já tiver transparência)
            new_alpha = cv2.min(a, edge_mask)
            scaled_fg_rgba = cv2.merge([b, g, r, new_alpha])

        # Sobrepõe a mídia principal (agora com borda suave) no canvas
        offset_x = params.get('posXFundo', 0); offset_y = params.get('posYFundo', 0)
        pos_x = int((frame_width - w_scaled) / 2) + offset_x
        pos_y = int((frame_height - h_scaled) / 2) + offset_y
        canvas = overlay_image(canvas, scaled_fg_rgba, pos_x, pos_y, 1.0)

    # Camada 3: Efeito de Vinheta (Fade) - CÓDIGO CORRIGIDO
    if img_fade.shape[:2] != (frame_height, frame_width):
        img_fade = cv2.resize(img_fade, (frame_width, frame_height), interpolation=cv2.INTER_AREA)

    # Separa os canais da imagem de fade (BGRA)
    fade_bgr = img_fade[:, :, :3]
    fade_alpha = img_fade[:, :, 3]

    # Normaliza as imagens para o intervalo [0, 1] para a multiplicação
    canvas_float = canvas.astype(np.float32) / 255.0
    fade_bgr_float = fade_bgr.astype(np.float32) / 255.0
    fade_alpha_float = fade_alpha.astype(np.float32) / 255.0
    
    # Cria uma máscara de alfa com 3 canais para a mesclagem
    alpha_mask = cv2.merge([fade_alpha_float, fade_alpha_float, fade_alpha_float])

    # Executa a multiplicação (modo de mesclagem "multiply")
    multiplied_layer = canvas_float * fade_bgr_float
    
    # Mescla a camada multiplicada com a original usando o alfa da vinheta
    # Fórmula: (foreground * alpha) + (background * (1 - alpha))
    blended_float = (multiplied_layer * alpha_mask) + (canvas_float * (1.0 - alpha_mask))
    
    # Converte de volta para 8-bit (0-255)
    imagem_com_fade = (blended_float * 255.0).astype(np.uint8)


    # Camada 4: Logo Urbnews
    if img_logo is not None:
        imagem_com_fade = overlay_image(imagem_com_fade, img_logo, params.get('posXLogo', 0), params.get('posYLogo', 0), params.get('escalaLogo', 1.0))
    
    pil_img = Image.fromarray(cv2.cvtColor(imagem_com_fade, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font_retranca = ImageFont.truetype(params['fontPath'], int(40 * params.get('escalaRetranca', 1.0)))
    font_titulo = ImageFont.truetype(params['fontPath'], params.get('fontSizeTitulo', 85))
    
    # Camadas 5 e 6: Box da Tag e Texto da Tag
    padding_x = 25; padding_y = 12; ajustebox = 4
    if format_key == "800x600": padding_x = 12; padding_y = 5; ajustebox = 3

    if hasattr(draw, 'textbbox'):
        bbox = draw.textbbox((0, 0), params['retranca'], font=font_retranca)
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    else: text_width, text_height = draw.textsize(params['retranca'], font=font_retranca)
    text_x, text_y = params['posXRetranca'], params['posYRetranca']
    
    box_x1, box_y1 = text_x - padding_x, text_y - padding_y
    box_x2, box_y2 = text_x + text_width + padding_x, text_y + text_height + padding_y + ajustebox
    draw.rectangle([(box_x1, box_y1), (box_x2, box_y2)], fill="white")
    draw.text((text_x, text_y), params['retranca'], font=font_retranca, fill="#005291")

    # Camada 7: Texto do Título
    max_width = frame_width - params.get('posXTitulo', 1000) - 50
    linhas_titulo = wrap_text(params['titulo'], font_titulo, max_width, tracking=params.get('letterSpacingTitulo', 0))
    y_text = params.get('posYTitulo', 280)
    for linha in linhas_titulo:
        draw_text_with_tracking(draw, (params.get('posXTitulo', 1000), y_text), linha, font_titulo, fill="white", tracking=params.get('letterSpacingTitulo', 0))
        line_height = font_titulo.getbbox("A")[3] if hasattr(font_titulo, 'getbbox') else font_titulo.getsize("A")[1]
        y_text += line_height + params.get('lineSpacingTitulo', 4)

    frame_com_texto = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    # Camada 8: Identidade Visual Animada
    if frame_identidade_bgr is not None:
        if frame_identidade_bgr.shape[:2] != (frame_height, frame_width): frame_identidade_bgr = cv2.resize(frame_identidade_bgr, (frame_width, frame_height))
        fade_start_time, fade_end_time = 3.0, 3.5
        current_time = frame_count / fps
        fade_opacity = 1.0
        if current_time >= fade_end_time: fade_opacity = 0.0
        elif current_time > fade_start_time: fade_opacity = 1.0 - ((current_time - fade_start_time) / (fade_end_time - fade_start_time))
        return cv2.addWeighted(frame_identidade_bgr, fade_opacity, frame_com_texto, 1.0 - fade_opacity, 0)
    return frame_com_texto

# (O resto do arquivo continua igual, sem alterações)
def render_video_for_format(format_key, assets, all_params):
    try:
        format_params = all_params['formats'][format_key]
        params = {**all_params, **format_params}
        fps = int(params.get('framerate', 30))
        total_frames = 10 * fps

        final_dimensions = tuple(map(int, format_key.split('x')))
        
        id_video_path = os.path.join(ASSETS_FOLDER, assets['base'])
        fade_img_path = os.path.join(ASSETS_FOLDER, assets['fade'])
        logo_img_path = os.path.join(ASSETS_FOLDER, "logo_urbnews.png")
        font_path = os.path.join(ASSETS_FOLDER, 'Figtree-Bold.ttf')
        params['fontPath'] = font_path

        if not all(os.path.exists(p) for p in [id_video_path, fade_img_path, logo_img_path, font_path]):
            raise FileNotFoundError(f"Assets não encontrados para {assets['label']}")

        id_video_reader = imageio.get_reader(id_video_path)
        img_fade = cv2.imread(fade_img_path, cv2.IMREAD_UNCHANGED)
        img_logo = cv2.imread(logo_img_path, cv2.IMREAD_UNCHANGED)
        
        user_media_path = os.path.join(BASE_DIR, params.get('userMediaFilename'))
        is_user_media_video = '.' in user_media_path and user_media_path.rsplit('.', 1)[1].lower() in ['mp4', 'webm', 'mov']
        user_media_reader = imageio.get_reader(user_media_path) if is_user_media_video else None
        user_img_bgr = cv2.imread(user_media_path) if not is_user_media_video else None

        date_str = datetime.now().strftime("%d%m%Y")
        retranca_str = re.sub(r'[^a-zA-Z0-9_]', '', params.get('retranca', 'RETRANCA')).upper()
        output_filename = f"{date_str}_{assets['label']}_URBNEWS_{retranca_str}.mp4"
        output_path = os.path.join(OUTPUT_FOLDER, output_filename)
        
        writer = cv2.VideoWriter(output_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, final_dimensions)
        
        for i in range(total_frames):
            try: id_rgb = id_video_reader.get_data(i)
            except IndexError: id_rgb = id_video_reader.get_data(id_video_reader.count_frames() - 1)
            id_bgr = cv2.cvtColor(id_rgb, cv2.COLOR_RGB2BGR)

            if is_user_media_video:
                try: user_rgb = user_media_reader.get_data(i)
                except IndexError: user_rgb = user_media_reader.get_data(user_media_reader.count_frames() - 1)
                frame_fundo = cv2.cvtColor(user_rgb, cv2.COLOR_RGB2BGR)
            else: frame_fundo = user_img_bgr

            final_frame = processar_frame(frame_fundo, img_logo, id_bgr, img_fade, i, fps, params, final_dimensions, format_key)
            writer.write(final_frame)
        
        writer.release()
        id_video_reader.close()
        if user_media_reader: user_media_reader.close()
        
        return {"url": f"/output/{output_filename}", "label": assets['label'], "path": output_path, "base_format": assets['label']}
    except Exception as e:
        print(f"[ERRO] ao renderizar {assets.get('label', 'formato desconhecido')}: {e}")
        return {"error": str(e), "label": assets.get('label', 'formato desconhecido')}

# --- Servidor Flask ---

app = Flask(__name__, template_folder=TEMPLATES_FOLDER, static_folder=STATIC_FOLDER)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload-media', methods=['POST'])
def upload_media():
    if 'userMedia' not in request.files: return jsonify({'error': 'Nenhum ficheiro enviado.'}), 400
    file = request.files['userMedia']
    if file.filename == '': return jsonify({'error': 'Nenhum ficheiro selecionado.'}), 400
    
    ext = file.filename.rsplit('.', 1)[1].lower()
    filename = f"user_media.{ext}"
    file_path = os.path.join(BASE_DIR, filename)
    file.save(file_path)
    return jsonify({'status': 'success', 'filename': filename})

@app.route('/preview-frame', methods=['POST'])
def preview_frame_endpoint():
    with preview_lock:
        try:
            params = {k: (float(v) if v.replace('.', '', 1).replace('-', '', 1).isdigit() else v) for k, v in request.form.items()}
            format_key = params.get('format', '1920x1080')
            if format_key not in FORMAT_ASSETS: return jsonify({'error': 'Formato inválido'}), 400
            
            assets = FORMAT_ASSETS[format_key]
            final_dimensions = tuple(map(int, format_key.split('x')))
            fps = int(params.get('framerate', 30))
            
            settings = json.load(open(SETTINGS_FILE_PATH)) if os.path.exists(SETTINGS_FILE_PATH) else {}
            user_media_path = os.path.join(BASE_DIR, settings.get('userMediaFilename'))
            if not os.path.exists(user_media_path): return jsonify({'error': 'Ficheiro de mídia não encontrado.'}), 400

            font_path = os.path.join(ASSETS_FOLDER, 'Figtree-Bold.ttf')
            logo_img_path = os.path.join(ASSETS_FOLDER, "logo_urbnews.png")
            fade_img_path = os.path.join(ASSETS_FOLDER, assets['fade'])
            
            params.update(settings)
            format_specific_settings = settings.get('formats', {}).get(format_key, {})
            combined_params = {**format_specific_settings, **params}
            combined_params['fontPath'] = font_path

            img_logo = cv2.imread(logo_img_path, cv2.IMREAD_UNCHANGED)
            img_fade = cv2.imread(fade_img_path, cv2.IMREAD_UNCHANGED)

            is_video = '.' in user_media_path and user_media_path.rsplit('.', 1)[1].lower() in ['mp4', 'webm', 'mov']
            if is_video:
                reader = imageio.get_reader(user_media_path)
                frame_fundo = cv2.cvtColor(reader.get_data(0), cv2.COLOR_RGB2BGR)
                reader.close()
            else:
                frame_fundo = cv2.imread(user_media_path)

            final_frame = processar_frame(frame_fundo, img_logo, None, img_fade, 5 * fps, fps, combined_params, final_dimensions, format_key)
            
            cv2.imwrite(PREVIEW_FILE_PATH, final_frame)
            return jsonify({'previewUrl': '/static/preview.jpg'})
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ERRO] Preview: {e}")
            return jsonify({'error': str(e)}), 500

@app.route('/generate-video', methods=['POST'])
def generate_video_endpoint():
    try:
        if not os.path.exists(SETTINGS_FILE_PATH): return jsonify({'error': "Ficheiro 'settings.json' não encontrado."}), 400
        with open(SETTINGS_FILE_PATH, 'r') as f: settings = json.load(f)
        
        base_results = [render_video_for_format(key, assets, settings) for key, assets in FORMAT_ASSETS.items()]
        
        generated_files = [res["path"] for res in base_results if "path" in res]
        derived_results = []

        for result in base_results:
            if "path" not in result: continue

            base_format_label = result.get("base_format")
            if not base_format_label: continue
            
            source_path = result["path"]
            
            derived_map = {
                "WIDEFULLHD": [("WIDE", (1280, 720), 10), ("TER", (1280, 720), 15)],
                "MUB-FOR-SP": [("LED4", (864, 288), 10)],
                "VERTFULLHD": [("VERT", (608, 1080), 10)]
            }

            if base_format_label in derived_map:
                for label, dims, duration in derived_map[base_format_label]:
                    try:
                        new_filename = os.path.basename(source_path).replace(base_format_label, label)
                        new_path = os.path.join(OUTPUT_FOLDER, new_filename)
                        
                        reader = cv2.VideoCapture(source_path)
                        fps = int(reader.get(cv2.CAP_PROP_FPS))
                        writer = cv2.VideoWriter(new_path, cv2.VideoWriter_fourcc(*'mp4v'), fps, dims)
                        
                        total_frames = duration * fps
                        source_frame_count = int(reader.get(cv2.CAP_PROP_FRAME_COUNT))
                        last_frame = None

                        for i in range(total_frames):
                            if i < source_frame_count:
                                ret, frame = reader.read()
                                if not ret: break
                                last_frame = frame
                            
                            if last_frame is None: continue
                            resized_frame = cv2.resize(last_frame, dims, interpolation=cv2.INTER_AREA)
                            writer.write(resized_frame)

                        reader.release()
                        writer.release()
                        derived_results.append({"url": f"/output/{new_filename}", "label": label, "path": new_path})
                    except Exception as e:
                        print(f"[ERRO] ao gerar formato derivado {label}: {e}")
                        derived_results.append({"error": str(e), "label": label})
        
        all_results = base_results + derived_results
        generated_files.extend([res["path"] for res in derived_results if "path" in res])

        zip_url = None
        if generated_files:
            date_str = datetime.now().strftime("%d%m%Y_%H%M")
            zip_filename = f"Urbnews_Videos_{date_str}.zip"
            zip_path = os.path.join(OUTPUT_FOLDER, zip_filename)
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for file_path in generated_files:
                    zipf.write(file_path, os.path.basename(file_path))
            zip_url = f"/output/{zip_filename}"

        return jsonify({"downloadUrls": all_results, "zipUrl": zip_url})

    except Exception as e:
        print(f"[ERRO] Geração de vídeo: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/load-settings', methods=['GET'])
def load_settings():
    if not os.path.exists(SETTINGS_FILE_PATH): return jsonify({}), 200
    with open(SETTINGS_FILE_PATH, 'r') as f: return jsonify(json.load(f))

@app.route('/save-settings', methods=['POST'])
def save_settings():
    with open(SETTINGS_FILE_PATH, 'w') as f: json.dump(request.json, f, indent=4)
    return jsonify({'status': 'success'})

@app.route('/output/<filename>')
def get_output_file(filename):
    return send_from_directory(OUTPUT_FOLDER, filename, as_attachment=True)

@app.route('/assets/<filename>')
def get_asset_file(filename):
    return send_from_directory(ASSETS_FOLDER, filename)

if __name__ == '__main__':
    for folder in [ASSETS_FOLDER, STATIC_FOLDER, TEMPLATES_FOLDER, UPLOAD_FOLDER, OUTPUT_FOLDER]:
        os.makedirs(folder, exist_ok=True)
    app.run(debug=True, port=5000)


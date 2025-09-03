// Variáveis globais para gerir o estado da aplicação
let fullSettings = {}, currentFormat = '1920x1080', isMediaUploaded = false;

// Referências a elementos do DOM
const form = document.getElementById('videoForm');
const formatSelector = document.getElementById('formatSelector');
const previewContainer = document.getElementById('previewContainer');
const generateBtn = document.getElementById('generateBtn');
const statusDiv = document.getElementById('status');
const previewImage = document.getElementById('previewImage');
const previewPlaceholder = document.getElementById('previewPlaceholder');
const previewStatus = document.getElementById('previewStatus');
const userMediaInput = document.getElementById('userMedia');
const userMediaNameSpan = document.getElementById('userMediaName');

// Configuração dos sliders
const sliderConfig = [
    {id: 'escalaRetranca', valueId: 'escalaRetrancaValue', unit: '', decimals: 2},
    {id: 'posXRetranca', valueId: 'posXRetrancaValue', unit: ' px', decimals: 0}, {id: 'posYRetranca', valueId: 'posYRetrancaValue', unit: ' px', decimals: 0},
    {id: 'posXTitulo', valueId: 'posXTituloValue', unit: ' px', decimals: 0}, {id: 'posYTitulo', valueId: 'posYTituloValue', unit: ' px', decimals: 0},
    {id: 'escalaFundo', valueId: 'escalaFundoValue', unit: '', decimals: 2}, {id: 'posXFundo', valueId: 'posXFundoValue', unit: ' px', decimals: 0},
    {id: 'posYFundo', valueId: 'posYFundoValue', unit: ' px', decimals: 0}, {id: 'blurFundo', valueId: 'blurFundoValue', unit: '', decimals: 0},
    {id: 'escalaLogo', valueId: 'escalaLogoValue', unit: '', decimals: 2}, {id: 'posXLogo', valueId: 'posXLogoValue', unit: ' px', decimals: 0},
    {id: 'posYLogo', valueId: 'posYLogoValue', unit: ' px', decimals: 0},
    {id: 'paddingXBox', valueId: 'paddingXBoxValue', unit: ' px', decimals: 0}, {id: 'paddingYBox', valueId: 'paddingYBoxValue', unit: ' px', decimals: 0},
    // NOVAS CONFIGURAÇÕES DE SLIDERS DA MÁSCARA
    {id: 'intensidadeMascara', valueId: 'intensidadeMascaraValue', unit: '', decimals: 2},
    {id: 'rotacaoMascara', valueId: 'rotacaoMascaraValue', unit: '°', decimals: 0},
    {id: 'posXMascara', valueId: 'posXMascaraValue', unit: ' px', decimals: 0},
    {id: 'posYMascara', valueId: 'posYMascaraValue', unit: ' px', decimals: 0}
];

// --- Funções Utilitárias ---
function debounce(func, timeout = 1200) {
    let timer;
    const debounced = (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => { func.apply(this, args); }, timeout);
    };
    debounced.cancel = () => { clearTimeout(timer); };
    return debounced;
}

// --- Funções de Manipulação da UI ---
function updateSliderDisplay(sliderId) {
    const config = sliderConfig.find(s => s.id === sliderId);
    if (!config) return;
    const slider = document.getElementById(config.id);
    const valueDisplay = document.getElementById(config.valueId);
    if (slider && valueDisplay) {
        valueDisplay.textContent = parseFloat(slider.value).toFixed(config.decimals) + config.unit;
    }
}

function loadControls() {
    document.getElementById('framerate').value = fullSettings.framerate || 30;
    document.getElementById('retranca').value = fullSettings.retranca || '';
    document.getElementById('titulo').value = fullSettings.titulo || '';
    
    const formatSettings = fullSettings.formats[currentFormat] || {};

    document.querySelectorAll('.control-input').forEach(input => {
        if (formatSettings.hasOwnProperty(input.name)) {
            input.value = formatSettings[input.name];
        } else {
            // Se o valor não estiver salvo, usa o valor padrão definido no HTML
            const defaultValue = input.getAttribute('value');
            if (defaultValue) {
                 input.value = defaultValue;
            }
        }
        if (input.type === 'range') {
            updateSliderDisplay(input.id);
        }
    });
}

function updateUIAfterFormatChange() {
    // Mostra/esconde os controlos de padding do Box
    const boxPaddingControls = document.getElementById('box-padding-controls');
    if (currentFormat === '800x600') {
        boxPaddingControls.classList.remove('hidden');
    } else {
        boxPaddingControls.classList.add('hidden');
    }

    let aspectClass = 'preview-aspect-16-9';
    if (currentFormat === '1080x1920') aspectClass = 'preview-aspect-9-16';
    if (currentFormat === '2048x720') aspectClass = 'preview-aspect-cinema';
    if (currentFormat === '800x600') aspectClass = 'preview-aspect-box';
    if (currentFormat === '960x1344') aspectClass = 'preview-aspect-abrigo';
    previewContainer.className = `w-full flex items-center justify-center rounded-lg overflow-hidden ${aspectClass}`;

    const [maxX, maxY] = currentFormat.split('x').map(Number);
    ['posXRetranca', 'posXTitulo', 'posXLogo'].forEach(id => document.getElementById(id).max = maxX);
    ['posYRetranca', 'posYTitulo', 'posYLogo'].forEach(id => document.getElementById(id).max = maxY);
}

// --- Funções de Lógica de Dados ---
function saveControls() {
    fullSettings.framerate = Number(document.getElementById('framerate').value);
    fullSettings.retranca = document.getElementById('retranca').value;
    fullSettings.titulo = document.getElementById('titulo').value;
    if (!fullSettings.formats) fullSettings.formats = {};
    if (!fullSettings.formats[currentFormat]) fullSettings.formats[currentFormat] = {};
    document.querySelectorAll('.control-input').forEach(input => {
        const value = (input.type === 'number' || input.type === 'range') ? Number(input.value) : input.value;
        fullSettings.formats[currentFormat][input.name] = value;
    });
}

// --- Funções de Comunicação com o Servidor (API) ---
async function saveSettingsToServer() {
    try {
        await fetch('/save-settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(fullSettings)
        });
    } catch (e) { console.error("Falha ao salvar configurações:", e); }
}

async function updatePreview() {
    if (!isMediaUploaded) return;
    previewStatus.textContent = "A atualizar preview...";
    
    const formData = new FormData(form);
    formData.append('format', currentFormat);
    document.querySelectorAll('.control-input-global').forEach(input => formData.append(input.name, input.value));

    try {
        const response = await fetch('/preview-frame', {method: 'POST', body: formData});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        
        previewImage.src = `${result.previewUrl}?t=${new Date().getTime()}`;
        previewImage.classList.remove('hidden');
        previewPlaceholder.classList.add('hidden');
        previewStatus.textContent = "Preview atualizado.";
    } catch (error) {
        previewStatus.textContent = `Erro no preview: ${error.message}`;
    }
}

const debouncedUpdate = debounce(() => {
    saveControls();
    saveSettingsToServer();
    updatePreview();
});

// --- Event Listeners ---
window.addEventListener('load', async () => {
    try {
        const response = await fetch('/load-settings');
        if (!response.ok) throw new Error('Falha ao contactar o servidor.');
        
        fullSettings = await response.json();
        isMediaUploaded = !!fullSettings.userMediaFilename;
        
        if(isMediaUploaded) userMediaNameSpan.textContent = fullSettings.userMediaOriginalFilename || fullSettings.userMediaFilename;

        currentFormat = fullSettings.selectedFormat || '1920x1080';
        formatSelector.value = currentFormat;
        
        updateUIAfterFormatChange();
        loadControls();
        
        if (isMediaUploaded) updatePreview();

    } catch (e) {
        console.error("Falha ao carregar a aplicação:", e);
        statusDiv.innerHTML = `<p class="text-red-500">Erro: Não foi possível ligar ao servidor.</p>`;
    }
    
    document.querySelectorAll('.control-input, .control-input-global').forEach(input => {
        input.addEventListener('input', debouncedUpdate);
        if (input.type === 'range') {
            input.addEventListener('input', () => updateSliderDisplay(input.id));
        }
    });

    // Lógica para secções retráteis
    document.querySelectorAll('.collapsible-trigger').forEach(trigger => {
        const content = trigger.nextElementSibling;
        const icon = trigger.querySelector('.collapsible-icon');
        const sectionId = trigger.parentElement.dataset.sectionId;

        if (localStorage.getItem(sectionId) === 'closed') {
            content.classList.remove('open');
            icon.classList.remove('open');
            trigger.classList.remove('open');
        } else {
            content.classList.add('open');
            icon.classList.add('open');
            trigger.classList.add('open');
        }

        trigger.addEventListener('click', () => {
            content.classList.toggle('open');
            icon.classList.toggle('open');
            trigger.classList.toggle('open');
            localStorage.setItem(sectionId, content.classList.contains('open') ? 'open' : 'closed');
        });
    });

    // Lógica para painel redimensionável
    const handle = document.getElementById('preview-divider');
    const controlsPanel = document.getElementById('controls-panel');
    const mainContainer = document.getElementById('main-container');
    let isResizing = false;

    const savedWidth = localStorage.getItem('controlsPanelWidth');
    if (savedWidth) {
        controlsPanel.style.width = savedWidth;
    }

    handle.addEventListener('mousedown', (e) => {
        isResizing = true;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        const containerRect = mainContainer.getBoundingClientRect();
        const newWidth = e.clientX - containerRect.left;
        const minWidth = 450; 
        const maxWidth = containerRect.width - 300; 
        if (newWidth > minWidth && newWidth < maxWidth) {
            controlsPanel.style.width = `${newWidth}px`;
        }
    });

    document.addEventListener('mouseup', () => {
        if (isResizing) {
            isResizing = false;
            document.body.style.cursor = 'default';
            document.body.style.userSelect = 'auto';
            localStorage.setItem('controlsPanelWidth', controlsPanel.style.width);
        }
    });
});

formatSelector.addEventListener('change', async function() {
    debouncedUpdate.cancel();
    saveControls();
    
    currentFormat = this.value;
    fullSettings.selectedFormat = currentFormat;
    
    updateUIAfterFormatChange();
    loadControls();
    
    await saveSettingsToServer();
    await updatePreview();
});

userMediaInput.addEventListener('change', async function() {
    const file = this.files[0];
    userMediaNameSpan.textContent = file ? file.name : 'Nenhum ficheiro selecionado';
    if (!file) { isMediaUploaded = false; return; }

    const formData = new FormData();
    formData.append('userMedia', file);
    previewStatus.textContent = "A enviar mídia...";

    try {
        const response = await fetch('/upload-media', {method: 'POST', body: formData});
        if (!response.ok) throw new Error((await response.json()).error);
        isMediaUploaded = true;
        
        fullSettings.userMediaFilename = `user_media.${file.name.split('.').pop()}`;
        fullSettings.userMediaOriginalFilename = file.name;
        
        await saveSettingsToServer();
        await updatePreview();
    } catch (e) {
        userMediaNameSpan.textContent = `Erro no upload: ${e.message}`;
        isMediaUploaded = false;
    }
});

generateBtn.addEventListener('click', async function() {
    if (!isMediaUploaded) {
        statusDiv.innerHTML = `<p class="text-red-500">Por favor, carregue a Mídia de Fundo primeiro.</p>`;
        return;
    }
    
    this.disabled = true;
    statusDiv.innerHTML = `<div class="flex items-center justify-center text-blue-600 dark:text-blue-400"><svg class="animate-spin -ml-1 mr-3 h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg><p>A processar todos os formatos...</p></div>`;
    
    saveControls();
    await saveSettingsToServer();

    try {
        const response = await fetch('/generate-video', {method: 'POST'});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error);
        
        let downloadLinksHTML = '<div class="space-y-3">';
        
        result.downloadUrls.forEach(item => {
            if(item.error) {
                downloadLinksHTML += `<p class="text-red-500 font-semibold">Falha ao gerar ${item.label}</p>`;
            } else {
                downloadLinksHTML += `<a href="${item.url}" target="_blank" class="block bg-gray-600 text-white font-bold py-2 px-4 rounded-lg hover:bg-gray-700 transition">Download ${item.label}</a>`;
            }
        });

        if (result.zipUrl) {
            downloadLinksHTML += `<hr class="my-4 border-gray-300 dark:border-gray-600">`;
            downloadLinksHTML += `<a href="${result.zipUrl}" target="_blank" class="block bg-[#005291] text-white font-bold py-3 px-4 rounded-lg hover:bg-[#003c6b] transition text-lg">Descarregar Todos (.zip)</a>`;
        }
        
        downloadLinksHTML += '</div>';
        statusDiv.innerHTML = downloadLinksHTML;

    } catch (e) {
        statusDiv.innerHTML = `<p class="text-red-500">Erro: ${e.message}</p>`;
    } finally {
        this.disabled = false;
    }
});


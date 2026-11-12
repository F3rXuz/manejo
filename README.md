# Jarvis - Asistente Inteligente para Windows con Gemini

Asistente local para Windows con soporte de voz y texto, impulsado por la API oficial de **Google Gemini** (`gemini-3.5-flash-lite` y respaldo automático en `gemini-3.8-flash`).

---

## 🚀 Novedades y Características

- **Voz Neural Ultra Natural:** Integración con **Edge TTS** (`es-AR-TomasNeural`) en streaming de audio directo mediante `sounddevice` y `miniaudio`.
- **Detección de Wake Words:** Activación por voz con palabras clave como *"Jarvis"*, *"Hola Jarvis"*, *"Che Jarvis"*, etc.
- **Acciones Múltiples Encadenadas:** Capacidad de ejecutar secuencias de tareas en una sola orden (ej: *"abre la calculadora y multiplica 50 por 4"* o *"abre el bloc de notas y escribe Hola"*).
- **Control de Windows Integrado:**
  - 🧮 Abrir calculadora y resolver cálculos automáticamente tecla a tecla.
  - 📝 Abrir bloc de notas y escribir texto al dictado.
  - 🌐 Abrir navegador web / búsquedas en Google y leer/resumir contenidos de páginas en voz alta.
  - 🖱️ Control visual del mouse (movimientos suaves, clics, doble clic y scroll).
  - 🔊 Control de volumen nativo de Windows (porcentajes, subir/bajar, silenciar).
  - 🔒 Bloquear pantalla de la PC.
  - 🧠 Responder dudas y consultas de conocimiento general.

---

## 🛠️ Instalación

1. Clona el repositorio:
   ```bash
   git clone https://github.com/F3rXuz/manejo.git
   cd manejo
   ```

2. Instala las dependencias necesarias:
   ```bash
   pip install -r requirements.txt
   ```

3. Configura tu API Key de Gemini:
   Crea un archivo `.env` en la raíz del proyecto con el contenido:
   ```env
   GEMINI_API_KEY=tu_api_key_aqui
   ```
   *(O configúrala en Windows con `setx GEMINI_API_KEY "tu_api_key"`).*

---

## 💻 Ejecución

* **Con doble clic:** Ejecuta `iniciar.bat`.
* **Desde consola:**
  ```powershell
  python main.py        # Menú interactivo
  python main.py -t     # Modo Texto directo
  python main.py -v     # Modo Voz directo
  ```

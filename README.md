# Asistente Inteligente para Windows con Gemini

Asistente local para Windows capaz de ser controlado tanto por **voz** como por **texto**, impulsado por la API oficial de **Google Gemini** (`gemini-3.5-flash-lite` con respaldo automático en `gemini-3.8-flash`).

---

## 🚀 Características principales

- **Control dual:** Manejo mediante micrófono (con palabra de activación `"asistente"`) o escribiendo órdenes directamente en consola.
- **Voz nativa en español:** Habla y confirma en voz alta cada acción que realiza en tiempo real usando Windows SAPI (`Microsoft Sabina Desktop - Spanish`).
- **Resiliencia y alta disponibilidad:** Tolerancia a fallos con reintento automático y respaldo de modelo ante picos de saturación (503).
- **Acciones integradas en Windows:**
  - 🌐 Abrir navegador web / búsquedas en Google y **lectura/resumen hablado del contenido de la página solicitada**
  - 🧮 Abrir calculadora
  - 📝 Abrir bloc de notas
  - 📂 Abrir carpeta de descargas
  - 🖱️ Mover cursor del mouse (al centro, por dirección o coordenadas) y hacer clics/scroll
  - 🔊 Control de volumen (establecer %, subir/bajar, silenciar)
  - 🔒 Bloquear la pantalla / PC
  - 🧠 Responder dudas y consultas de conocimiento general

---

## 🛠️ Instalación y Requisitos

Todas las dependencias están instaladas y registradas en `requirements.txt`:
```powershell
pip install -r requirements.txt
```

### Configuración de la API Key:
```powershell
setx GEMINI_API_KEY "tu_api_key_aqui"
```

---

## 💻 Formas de inicio

1. **Doble clic:** Ejecutar el archivo `iniciar.bat`.
2. **Modo Menú:**
   ```powershell
   python main.py
   ```
3. **Directo Modo Texto:**
   ```powershell
   python main.py -t
   ```
4. **Directo Modo Voz:**
   ```powershell
   python main.py -v
   ```


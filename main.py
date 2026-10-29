import os
import sys
import time
import json
import re
import html
import ctypes
import winreg
import argparse
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional, Any

import requests
import speech_recognition as sr
import pyttsx3
import pyautogui
from google import genai
from google.genai import types

# Control de volumen en Windows
try:
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from comtypes import CLSCTX_ALL
    from ctypes import cast, POINTER
    PYCAW_AVAILABLE = True
except Exception:
    PYCAW_AVAILABLE = False

# ==============================================================================
# CONFIGURACIÓN
# ==============================================================================
MODEL_ID = "gemini-3.5-flash-lite"
FALLBACK_MODELS = ["gemini-3.8-flash"]
WAKE_WORD = "asistente"
LANGUAGE_CODE = "es-ES"

SYSTEM_INSTRUCTION = """
Eres un asistente inteligente para Windows en idioma español.
Tu función es interpretar las órdenes o consultas del usuario (recibidas por voz o texto) y responder EXCLUSIVAMENTE con un JSON válido con esta estructura exacta:
{
    "action": "open_browser" | "open_calculator" | "open_notepad" | "open_downloads" | "set_volume" | "change_volume" | "mute_volume" | "lock_pc" | "move_mouse" | "click_mouse" | "scroll_mouse" | "type_text" | "none",
    "parameter": valor_o_null,
    "spoken_response": "Frase breve hablada en presente o gerundio indicando exactamente la acción que vas a realizar (ej: 'Abriendo la calculadora', 'Abriendo el navegador', 'Moviendo el mouse', 'Ajustando el volumen', 'Bloqueando la computadora')."
}

REGLA OBLIGATORIA PARA 'spoken_response':
- Cada vez que recibas una orden de ejecutar una acción en el sistema (abrir aplicaciones, controlar volumen, mover mouse, bloquear PC, etc.), DEBES indicar en voz alta lo que estás haciendo en gerundio o presente inmediato (ejemplos: "Abriendo la calculadora", "Abriendo el bloc de notas", "Abriendo el navegador", "Abriendo descargas", "Moviendo el mouse", "Ajustando el volumen al 50%", "Bloqueando la computadora").
- Si es una pregunta de conocimiento general, saluda o responde la consulta en máximo 2 a 3 oraciones breves.

Detalles de las acciones:
- "open_browser": Abrir navegador web o buscar algo en internet.
  En 'parameter' envía un objeto:
  {"url": "URL completa o consulta de búsqueda", "read_page": true | false}
  (O directamente el texto con la URL o consulta).
  REGLA DE LECTURA WEB: Si el usuario pide abrir una página específica (ej: Wikipedia, diarios, documentación, blogs, python.org, clarin.com, etc.) o pide explícitamente "qué hay en la página", "qué dice", "dime qué hay", "léela" o "cuéntame de qué trata", pon siempre "read_page": true para que el asistente analice el contenido web y lo explique en voz alta.
- "open_calculator": Abrir la calculadora. 'parameter': null.
- "open_notepad": Abrir el bloc de notas. 'parameter': null.
- "open_downloads": Abrir la carpeta de descargas del sistema. 'parameter': null.
- "set_volume": Establecer el volumen a un porcentaje específico (ej: 'volumen al 40%'). 'parameter': número entero entre 0 y 100.
- "change_volume": Subir o bajar el volumen de forma relativa (ej: 'sube el volumen', 'baja un poco'). 'parameter': entero con el cambio relativo (+15 para subir, -15 para bajar).
- "mute_volume": Silenciar o activar el sonido. 'parameter': true para silenciar, false para reactivar sonido.
- "lock_pc": Bloquear la computadora / bloquear pantalla. 'parameter': null.
- "move_mouse": Mover el puntero del mouse o cursor según la petición del usuario.
  En 'parameter' envía un objeto con una de estas tres modalidades:
    1) Movimiento relativo (ej: "mueve el mouse hacia arriba", "mueve el cursor 200 píxeles a la derecha", "baja un poco el mouse"):
       {"type": "relative", "dx": entero, "dy": entero}
       (Nota: derecha dx>0, izquierda dx<0, abajo dy>0, arriba dy<0. Si no especifican píxeles, usa ~150-250 píxeles por defecto).
    2) Posiciones clave (ej: "mueve el mouse al centro", "a la esquina superior derecha", "arriba a la izquierda"):
       {"type": "position", "target": "center" | "top_left" | "top_right" | "bottom_left" | "bottom_right"}
    3) Coordenadas exactas (ej: "mueve el mouse a 500, 300"):
       {"type": "absolute", "x": entero, "y": entero}
- "click_mouse": Hacer clic con el mouse (ej: "haz clic", "clic derecho", "doble clic"). 'parameter': "left" | "right" | "double".
- "scroll_mouse": Desplazamiento o scroll (ej: "baja en la pantalla", "scroll hacia arriba"). 'parameter': entero (+300 para arriba, -300 para abajo).
- "type_text": Escribir texto en la ventana activa (bloc de notas, Word, buscador, cualquier campo de texto).
  Usar cuando el usuario pida: "escribe", "escríbeme", "anota", "dicta", "pon el texto", "escribe en el bloc", etc.
  En 'parameter' envía el texto completo a escribir como string.
  Si el usuario pide abrir el bloc Y escribir en el mismo mensaje, abre primero con "open_notepad" y en el spoken_response avisá que luego escribirás; el usuario deberá pedir de nuevo que escribas.
- "none": Preguntas de conocimiento general, dudas, clima, hora, conversaciones o saludos.
"""

# ==============================================================================
# MOTOR TEXT-TO-SPEECH (pyttsx3 + respaldo SAPI directo)
# ==============================================================================
def init_sapi_voice():
    """Inicializa la voz nativa de Windows (SAPI.SpVoice) como motor de alta confiabilidad."""
    try:
        import win32com.client
        sp = win32com.client.Dispatch("SAPI.SpVoice")
        sp.Volume = 100
        for v in sp.GetVoices():
            desc = v.GetDescription().lower()
            if any(term in desc for term in ["sabina", "spanish", "español", "helena", "raul"]):
                sp.Voice = v
                break
        return sp
    except Exception:
        return None

sapi_voice = init_sapi_voice()

def init_tts_engine() -> Optional[pyttsx3.Engine]:
    """Inicializa pyttsx3 configurando una voz en español si está disponible."""
    try:
        engine = pyttsx3.init("sapi5")
        engine.setProperty("volume", 1.0)
        engine.setProperty("rate", 175)
        voices = engine.getProperty("voices")
        for voice in voices:
            name_lower = voice.name.lower()
            if any(v in name_lower for v in ["spanish", "español", "sabina", "helena", "raul", "pablo"]):
                engine.setProperty("voice", voice.id)
                break
        return engine
    except Exception as e:
        return None

tts_engine = init_tts_engine()

def speak(text: str):
    """Pronuncia el texto en voz alta y lo imprime en consola siempre sin omitir órdenes consecutivas."""
    print(f"\n[Asistente]: {text}\n")

    # 1. Usar SAPI nativo directo de Windows (infalible en llamadas consecutivas)
    global sapi_voice
    if sapi_voice:
        try:
            sapi_voice.Speak(text)
            return
        except Exception:
            pass

    # 2. Respaldo con pyttsx3
    try:
        engine = pyttsx3.init("sapi5")
        engine.setProperty("volume", 1.0)
        engine.setProperty("rate", 175)
        voices = engine.getProperty("voices")
        for voice in voices:
            name_lower = voice.name.lower()
            if any(v in name_lower for v in ["spanish", "español", "sabina", "helena", "raul", "pablo"]):
                engine.setProperty("voice", voice.id)
                break
        engine.say(text)
        engine.runAndWait()
        del engine
    except Exception as e:
        print(f"[Aviso TTS]: {e}")

# ==============================================================================
# CONTROL DE VOLUMEN (pycaw + fallback pyautogui)
# ==============================================================================
def get_volume_interface():
    """Obtiene la interfaz de control de volumen de Windows vía pycaw."""
    if not PYCAW_AVAILABLE:
        return None
    try:
        devices = AudioUtilities.GetSpeakers()
        if hasattr(devices, "EndpointVolume") and devices.EndpointVolume:
            return devices.EndpointVolume
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))
    except Exception:
        return None

def set_volume_percent(percent: float):
    """Ajusta el volumen a un porcentaje exacto (0-100)."""
    target = max(0.0, min(100.0, float(percent)))
    vol = get_volume_interface()
    if vol:
        try:
            vol.SetMasterVolumeLevelScalar(target / 100.0, None)
            return
        except Exception:
            pass
    # Fallback con pyautogui
    pyautogui.press("volumemute")
    steps = int(target / 2)
    pyautogui.press("volumeup", presses=steps)

def change_volume_relative(delta_percent: float):
    """Sube o baja el volumen de forma relativa."""
    vol = get_volume_interface()
    if vol:
        try:
            current = vol.GetMasterVolumeLevelScalar() * 100.0
            target = max(0.0, min(100.0, current + float(delta_percent)))
            vol.SetMasterVolumeLevelScalar(target / 100.0, None)
            return
        except Exception:
            pass
    # Fallback con pyautogui
    steps = max(1, int(abs(delta_percent) / 2))
    key = "volumeup" if delta_percent > 0 else "volumedown"
    pyautogui.press(key, presses=steps)

def set_mute_state(mute: bool):
    """Silencia o reactiva el volumen."""
    vol = get_volume_interface()
    if vol:
        try:
            vol.SetMute(1 if mute else 0, None)
            return
        except Exception:
            pass
    pyautogui.press("volumemute")

# ==============================================================================
# LECTURA Y RESUMEN DE PÁGINAS WEB
# ==============================================================================
def extract_text_from_html(html_content: str) -> str:
    """Extrae texto legible de un documento HTML eliminando etiquetas, scripts y estilos."""
    clean = re.sub(r'<script[^>]*>.*?</script>', ' ', html_content, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<style[^>]*>.*?</style>', ' ', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<noscript[^>]*>.*?</noscript>', ' ', clean, flags=re.DOTALL | re.IGNORECASE)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = html.unescape(clean)
    return re.sub(r'\s+', ' ', clean).strip()

def summarize_webpage_content(client: genai.Client, url: str):
    """Descarga el contenido de la página web solicitada y explica en voz alta qué contiene."""
    try:
        if not (url.startswith("http://") or url.startswith("https://")):
            return

        print(f"\n[Analizando contenido de: {url} ...]")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=6)
        if resp.status_code != 200:
            return

        text = extract_text_from_html(resp.text)
        if len(text) < 80:
            return

        prompt = (
            "Resume brevemente en 2 o 3 oraciones claras, fluidas y naturales en español "
            "qué contiene o de qué trata esta página web para decírselo al usuario en voz alta:\n\n"
            f"{text[:3500]}"
        )
        summary_resp = client.models.generate_content(
            model=MODEL_ID,
            contents=prompt,
        )
        if summary_resp and summary_resp.text:
            summary = summary_resp.text.strip().replace("*", "").replace("#", "")
            speak(f"En la página encontré lo siguiente: {summary}")
    except Exception as e:
        print(f"[Aviso lectura web]: {e}")

# ==============================================================================
# UTILIDAD: ENFOCAR VENTANA POR TÍTULO
# ==============================================================================
def focus_window_by_title(title_keyword: str) -> bool:
    """Busca una ventana cuyo título contenga la palabra clave y la pone al frente."""
    try:
        import win32gui
        import win32con

        def callback(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title_keyword.lower() in title.lower():
                    results.append(hwnd)

        hwnds = []
        win32gui.EnumWindows(callback, hwnds)
        if hwnds:
            hwnd = hwnds[0]
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
            time.sleep(0.3)
            return True
    except Exception as e:
        print(f"[Aviso] No se pudo enfocar ventana '{title_keyword}': {e}")
    return False


def execute_system_action(client: genai.Client, action: str, parameter: Any):
    """Ejecuta la acción solicitada en Windows."""
    try:
        if action == "open_browser":
            target_url = None
            should_read = False

            if isinstance(parameter, dict):
                target_url = parameter.get("url")
                should_read = bool(parameter.get("read_page", False))
            elif isinstance(parameter, str):
                target_url = parameter

            final_url = "https://www.google.com"
            if target_url:
                if target_url.startswith("http://") or target_url.startswith("https://"):
                    final_url = target_url
                elif "." in target_url and " " not in target_url:
                    final_url = f"https://{target_url}"
                else:
                    final_url = f"https://www.google.com/search?q={target_url}"

            webbrowser.open(final_url)

            # Si se solicitó leer la página o es una URL directa (ej: Clarín, Wikipedia, etc.)
            if should_read or (target_url and "." in target_url and "google.com/search" not in final_url):
                summarize_webpage_content(client, final_url)

        elif action == "open_calculator":
            subprocess.Popen(["calc.exe"])

        elif action == "open_notepad":
            subprocess.Popen(["notepad.exe"])
            time.sleep(1.2)  # Esperar a que el bloc de notas esté listo
            focus_window_by_title("bloc de notas")

        elif action == "open_downloads":
            downloads_dir = Path.home() / "Downloads"
            if downloads_dir.exists():
                os.startfile(str(downloads_dir))
            else:
                subprocess.Popen(["explorer.exe", "shell:Downloads"])

        elif action == "set_volume":
            if parameter is not None:
                set_volume_percent(float(parameter))

        elif action == "change_volume":
            delta = float(parameter) if parameter is not None else 15.0
            change_volume_relative(delta)

        elif action == "mute_volume":
            is_mute = bool(parameter) if parameter is not None else True
            set_mute_state(is_mute)

        elif action == "lock_pc":
            ctypes.windll.user32.LockWorkStation()

        elif action == "move_mouse":
            screen_w, screen_h = pyautogui.size()
            if isinstance(parameter, dict):
                m_type = parameter.get("type", "relative")
                if m_type == "relative":
                    dx = int(parameter.get("dx", 0))
                    dy = int(parameter.get("dy", 0))
                    pyautogui.moveRel(dx, dy, duration=0.3)
                elif m_type == "absolute":
                    x = int(parameter.get("x", screen_w // 2))
                    y = int(parameter.get("y", screen_h // 2))
                    pyautogui.moveTo(x, y, duration=0.3)
                elif m_type == "position":
                    target = str(parameter.get("target", "center")).lower()
                    positions = {
                        "center": (screen_w // 2, screen_h // 2),
                        "top_left": (50, 50),
                        "top_right": (screen_w - 50, 50),
                        "bottom_left": (50, screen_h - 50),
                        "bottom_right": (screen_w - 50, screen_h - 50),
                    }
                    pos = positions.get(target, (screen_w // 2, screen_h // 2))
                    pyautogui.moveTo(pos[0], pos[1], duration=0.3)
            elif isinstance(parameter, str):
                target = parameter.lower()
                if "centro" in target or target == "center":
                    pyautogui.moveTo(screen_w // 2, screen_h // 2, duration=0.3)
                elif "arriba" in target:
                    pyautogui.moveRel(0, -200, duration=0.3)
                elif "abajo" in target:
                    pyautogui.moveRel(0, 200, duration=0.3)
                elif "derecha" in target:
                    pyautogui.moveRel(200, 0, duration=0.3)
                elif "izquierda" in target:
                    pyautogui.moveRel(-200, 0, duration=0.3)

        elif action == "click_mouse":
            btn = str(parameter).lower() if parameter else "left"
            if btn == "right":
                pyautogui.rightClick()
            elif btn == "double":
                pyautogui.doubleClick()
            else:
                pyautogui.click()

        elif action == "scroll_mouse":
            amount = int(parameter) if parameter is not None else -300
            pyautogui.scroll(amount)

        elif action == "type_text":
            if parameter and isinstance(parameter, str):
                # Intentar enfocar ventana activa de texto (Bloc de notas, Word, etc.)
                focused = focus_window_by_title("bloc de notas")
                if not focused:
                    focus_window_by_title("notepad")
                if not focused:
                    time.sleep(0.3)  # Al menos una pequeña pausa
                # Usar portapapeles para soportar acentos y caracteres especiales
                try:
                    safe_text = parameter.replace("'", "\\'")
                    subprocess.run(
                        ["powershell", "-command", f"Set-Clipboard -Value '{safe_text}'"],
                        check=True, capture_output=True
                    )
                    pyautogui.hotkey("ctrl", "v")
                except Exception:
                    # Fallback: escribir carácter por carácter
                    pyautogui.write(parameter, interval=0.03)

        elif action == "none":
            pass

        else:
            print(f"[Acción no reconocida]: {action}")

    except Exception as e:
        print(f"[Error al ejecutar acción '{action}']: {e}")

# ==============================================================================
# PROCESAMIENTO CON GEMINI (google-genai)
# ==============================================================================
def get_gemini_client() -> genai.Client:
    """Inicializa el cliente de Google GenAI."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        # Intentar leer desde el registro de Windows si se guardó con setx
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                val, _ = winreg.QueryValueEx(key, "GEMINI_API_KEY")
                if val:
                    os.environ["GEMINI_API_KEY"] = val
                    api_key = val
        except Exception:
            pass

    if not api_key:
        print("\n" + "!" * 65)
        print("[ADVERTENCIA] No se detectó la variable de entorno GEMINI_API_KEY.")
        print("Configúrala antes de realizar consultas a Gemini ejecutando en PowerShell:")
        print('   $env:GEMINI_API_KEY = "tu_api_key_aqui"')
        print("O de manera permanente con:")
        print('   setx GEMINI_API_KEY "tu_api_key_aqui"')
        print("!" * 65 + "\n")
    return genai.Client(api_key=api_key)

def process_command_with_gemini(client: genai.Client, user_query: str) -> Optional[dict]:
    """Envía la consulta a Gemini con reintentos y modelo de respaldo si hay saturación (503)."""
    models_to_try = [MODEL_ID] + [m for m in FALLBACK_MODELS if m != MODEL_ID]

    for current_model in models_to_try:
        for attempt in range(2):
            try:
                response = client.models.generate_content(
                    model=current_model,
                    contents=user_query,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )

                raw_text = response.text.strip()
                if raw_text.startswith("```"):
                    lines = raw_text.splitlines()
                    if lines and lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    raw_text = "\n".join(lines).strip()

                data = json.loads(raw_text)
                return data

            except Exception as e:
                err_str = str(e)
                # Si el modelo está saturado temporalmente (503 / 429)
                if "503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str:
                    if attempt == 0:
                        time.sleep(1)
                        continue
                    else:
                        print(f"[{current_model} saturado temporalmente. Probando con modelo de respaldo...]")
                        break
                else:
                    print(f"[Error Gemini con {current_model}]: {e}")
                    break

    return None

DEFAULT_ACTION_SPEECH = {
    "open_calculator": "Abriendo la calculadora.",
    "open_notepad": "Abriendo el bloc de notas.",
    "open_browser": "Abriendo el navegador.",
    "open_downloads": "Abriendo la carpeta de descargas.",
    "set_volume": "Ajustando el volumen.",
    "change_volume": "Cambiando el volumen.",
    "mute_volume": "Modificando el audio.",
    "lock_pc": "Bloqueando la computadora.",
    "move_mouse": "Moviendo el mouse.",
    "click_mouse": "Haciendo clic.",
    "scroll_mouse": "Desplazando la pantalla.",
    "type_text": "Escribiendo el texto.",
}

def handle_user_command(client: genai.Client, command: str):
    """Procesa una orden interpretada por Gemini, anuncia la acción hablada y la ejecuta."""
    print(f"[Interpretando con {MODEL_ID}...]")
    result = process_command_with_gemini(client, command)

    if result:
        action = result.get("action", "none")
        param = result.get("parameter")
        spoken_reply = result.get("spoken_response")

        # Asegurar confirmación hablada clara y descriptiva
        if not spoken_reply and action in DEFAULT_ACTION_SPEECH:
            spoken_reply = DEFAULT_ACTION_SPEECH[action]
        elif not spoken_reply:
            spoken_reply = "Entendido."

        # 1. Hablar la acción primero (ej: "Abriendo la calculadora.")
        speak(spoken_reply)

        # 2. Ejecutar la orden en el sistema inmediatamente
        if action and action != "none":
            execute_system_action(client, action, param)
    else:
        speak("Lo siento, no pude procesar la solicitud en este momento.")

# ==============================================================================
# MODO TEXTO
# ==============================================================================
def run_text_mode(client: genai.Client):
    """Bucle de interacción mediante texto escrito por consola."""
    print("\n" + "=" * 65)
    print("                 MODO TEXTO ACTIVADO                         ")
    print("=" * 65)
    print("Escribe tus órdenes directamente (no requieres decir 'asistente').")
    print("Ejemplos:")
    print("  - abre la calculadora")
    print("  - abre clarin.com y dime qué hay")
    print("  - abre wikipedia de los dinosaurios")
    print("  - mueve el mouse hacia arriba / al centro")
    print("  - haz clic / doble clic / clic derecho")
    print("  - pon el volumen al 50")
    print("  - abre descargas")
    print("  - ¿qué día llegó el hombre a la luna?")
    print("Escribe 'salir' o 'exit' para terminar.\n")

    while True:
        try:
            user_input = input("[Tú]: ").strip()
            if not user_input:
                continue

            if user_input.lower() in ["salir", "exit", "quit", "terminar"]:
                speak("Hasta luego.")
                break

            # Limpiar si el usuario escribe 'asistente' al inicio por costumbre
            cleaned_command = user_input
            if cleaned_command.lower().startswith(WAKE_WORD):
                cleaned_command = cleaned_command[len(WAKE_WORD):].lstrip(",. ")

            handle_user_command(client, cleaned_command if cleaned_command else user_input)

        except (KeyboardInterrupt, EOFError):
            print("\n[Saliendo del modo texto...]")
            speak("Hasta luego.")
            break

# ==============================================================================
# MODO VOZ
# ==============================================================================
def listen_microphone(recognizer: sr.Recognizer, source: sr.Microphone, timeout: Optional[int] = None, phrase_time_limit: int = 8) -> Optional[str]:
    """Escucha por el micrófono y transcribe la voz usando SpeechRecognition."""
    try:
        audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        text = recognizer.recognize_google(audio, language=LANGUAGE_CODE)
        return text.strip()
    except (sr.WaitTimeoutError, sr.UnknownValueError):
        return None
    except sr.RequestError as e:
        print(f"[Error de conexión en reconocimiento de voz]: {e}")
        return None

def run_voice_mode(client: genai.Client):
    """Bucle de interacción mediante voz con palabra de activación.
    Se añaden mejoras para manejar la calibración del micrófono y errores de detección.
    """
    print("\n" + "=" * 65)
    print("                 MODO VOZ ACTIVADO                          ")
    print("=" * 65)
    print(f"Palabra de activación: '{WAKE_WORD.upper()}'")
    print("Ejemplos:")
    print("  - 'Asistente, abre la calculadora'")
    print("  - 'Asistente, sube el volumen'")
    print("  - 'Asistente, ¿cuál es la capital de Italia?'")
    print("Presiona Ctrl+C para salir.\n")

    recognizer = sr.Recognizer()
    recognizer.dynamic_energy_threshold = True

    # Listar dispositivos de micrófono disponibles
    mic_names = []
    try:
        mic_names = sr.Microphone.list_microphone_names()
        print("[Micrófonos disponibles]:")
        for idx, name in enumerate(mic_names):
            print(f"  [{idx}] {name}")
    except Exception as e:
        print(f"[Aviso] No se pudo listar micrófonos: {e}")

    # Buscar el mejor micrófono disponible (priorizar palabras clave de mic real)
    preferred_keywords = ["moto", "microfono", "microphone", "mic", "input", "captura"]
    skip_keywords = ["output", "altavoz", "speaker", "mezcla", "stereo mix", "asignador"]
    chosen_index = None

    for idx, name in enumerate(mic_names):
        name_lower = name.lower()
        if any(sk in name_lower for sk in skip_keywords):
            continue
        if any(pk in name_lower for pk in preferred_keywords):
            chosen_index = idx
            break

    # Intentar abrir el micrófono seleccionado
    mic = None
    if chosen_index is not None:
        try:
            mic = sr.Microphone(device_index=chosen_index)
            print(f"[Info] Usando micrófono [{chosen_index}]: {mic_names[chosen_index]}")
        except Exception as e:
            print(f"[Aviso] No se pudo abrir micrófono [{chosen_index}]: {e}")

    if mic is None:
        try:
            mic = sr.Microphone()
            print("[Info] Usando micrófono por defecto del sistema.")
        except Exception as e:
            print(f"[Error crítico] No se pudo abrir ningún micrófono: {e}")
            speak("No se detectó un micrófono disponible. Cambia a modo texto.")
            return

    # Umbral fijo: sin calibración para evitar bloqueos
    recognizer.energy_threshold = 300
    recognizer.dynamic_energy_threshold = True

    try:
        with mic as source:
            print("[Listo] Micrófono activo. Di 'asistente' seguido de tu orden...\n")
            speak("Asistente listo. Puedes hablar.")
            while True:
                try:
                    detected_text = listen_microphone(recognizer, source, timeout=5, phrase_time_limit=8)
                    if not detected_text:
                        continue
                    print(f"[Escuchado]: {detected_text}")
                    text_lower = detected_text.lower()
                    if WAKE_WORD in text_lower:
                        parts = text_lower.split(WAKE_WORD, 1)
                        command = parts[1].strip().lstrip(",. ") if len(parts) > 1 else ""
                        if not command:
                            speak("Sí, te escucho.")
                            command = listen_microphone(recognizer, source, timeout=6, phrase_time_limit=10)
                            if not command:
                                speak("No te escuché. Avísame si necesitas algo.")
                                continue
                        handle_user_command(client, command)
                except sr.WaitTimeoutError:
                    continue
                except (KeyboardInterrupt, EOFError):
                    print("\n[Saliendo del modo voz...]")
                    speak("Hasta luego.")
                    break
                except Exception as loop_err:
                    print(f"[Error en bucle]: {loop_err}")
    except (KeyboardInterrupt, SystemExit):
        print("\n[Deteniendo modo voz...]")
        speak("Hasta pronto.")
    except Exception as e:
        print(f"[Error con el micrófono]: {e}")
        print("Cambiando automáticamente al Modo Texto...\n")
        run_text_mode(client)






def check_audio_output():
    """Verifica el estado del dispositivo de audio y nivel de volumen de Windows."""
    try:
        if PYCAW_AVAILABLE:
            sp = AudioUtilities.GetSpeakers()
            vol = getattr(sp, "volume_percent", None)
            dev_name = getattr(sp, "FriendlyName", "Dispositivo de audio")
            if vol is not None:
                if vol < 30.0:
                    print(f"\n[Aviso Audio] Salida de sonido: '{dev_name}' (Volumen al {vol:.0f}% - Muy bajo).")
                    print("  Recomendación: Sube el volumen de tu PC o conecta auriculares para escuchar con claridad.\n")
                else:
                    print(f"[Audio]: Salida activa: '{dev_name}' (Volumen: {vol:.0f}%)\n")
    except Exception:
        pass

# ==============================================================================
# PUNTO DE ENTRADA PRINCIPAL
# ==============================================================================
def main():
    check_audio_output()
    parser = argparse.ArgumentParser(description="Asistente para Windows con Gemini")
    parser.add_argument("-t", "--texto", action="store_true", help="Iniciar directamente en modo texto")
    parser.add_argument("-v", "--voz", action="store_true", help="Iniciar directamente en modo voz")
    args = parser.parse_args()

    client = get_gemini_client()

    if args.texto:
        run_text_mode(client)
    elif args.voz:
        run_voice_mode(client)
    else:
        print("=" * 65)
        print("          ASISTENTE INTELIGENTE PARA WINDOWS                 ")
        print("=" * 65)
        print("Selecciona cómo deseas interactuar:")
        print("  [1] Modo Voz  (Micrófono con palabra 'asistente')")
        print("  [2] Modo Texto (Escribir órdenes por teclado)")
        print("-" * 65)

        opcion = input("Elige una opción [1/2] (Presiona Enter para Voz): ").strip()

        if opcion == "2":
            run_text_mode(client)
        else:
            run_voice_mode(client)

if __name__ == "__main__":
    main()

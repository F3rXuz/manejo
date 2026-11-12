const express = require('express');
const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion
} = require('@whiskeysockets/baileys');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const path = require('path');

const app = express();
app.use(express.json());

const PORT = 3000;
let sock = null;
let qrCodeString = null;
let isConnected = false;
let userJid = null;

// Almacén en memoria de los últimos mensajes entrantes para Jarvis
const recentMessages = [];
const MAX_MESSAGES = 40;

async function connectToWhatsApp() {
    const authPath = path.join(__dirname, 'auth_info_baileys');
    const { state, saveCreds } = await useMultiFileAuthState(authPath);
    const { version, isLatest } = await fetchLatestBaileysVersion();

    console.log(`[WhatsApp Service] Iniciando Baileys v${version.join('.')}, isLatest: ${isLatest}`);

    sock = makeWASocket({
        version,
        logger: pino({ level: 'silent' }),
        printQRInTerminal: false,
        auth: state,
        browser: ['Jarvis Windows Assistant', 'Chrome', '1.0.0']
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            qrCodeString = qr;
            console.log('\n============================================================');
            console.log('   ESCANEA ESTE CÓDIGO QR CON TU WHATSAPP (Dispositivos vinculados)');
            console.log('============================================================\n');
            qrcode.generate(qr, { small: true });
            console.log('\n============================================================\n');
        }

        if (connection === 'close') {
            isConnected = false;
            const statusCode = lastDisconnect?.error?.output?.statusCode;
            const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
            console.log(`[WhatsApp Service] Conexión cerrada (${statusCode}). Reintentando: ${shouldReconnect}`);
            if (shouldReconnect) {
                setTimeout(connectToWhatsApp, 3000);
            }
        } else if (connection === 'open') {
            isConnected = true;
            qrCodeString = null;
            userJid = sock.user.id;
            console.log(`\n>>> [WhatsApp Service] ¡CONECTADO CON ÉXITO! Usuario: ${userJid} <<<\n`);
        }
    });

    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify') return;

        for (const msg of messages) {
            // Ignorar mensajes enviados por nosotros mismos
            if (msg.key.fromMe) continue;

            const from = msg.key.remoteJid;
            const pushName = msg.pushName || 'Desconocido';
            const messageContent =
                msg.message?.conversation ||
                msg.message?.extendedTextMessage?.text ||
                msg.message?.imageMessage?.caption ||
                '[Contenido multimedia / adjunto]';

            const timestamp = new Date((msg.messageTimestamp || Date.now() / 1000) * 1000);

            console.log(`[Mensaje Recibido] De: ${pushName} (${from}): ${messageContent}`);

            recentMessages.unshift({
                id: msg.key.id,
                from,
                senderName: pushName,
                text: messageContent,
                time: timestamp.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })
            });

            if (recentMessages.length > MAX_MESSAGES) {
                recentMessages.pop();
            }
        }
    });
}

// -----------------------------------------------------------------------------
// ENDPOINTS HTTP PARA JARVIS (main.py)
// -----------------------------------------------------------------------------

// 1. Estado de la conexión
app.get('/status', (req, res) => {
    res.json({
        connected: isConnected,
        hasQr: !!qrCodeString,
        user: userJid
    });
});

// 2. Obtener los últimos mensajes recibidos
app.get('/messages', (req, res) => {
    const limit = parseInt(req.query.limit) || 5;
    res.json({
        connected: isConnected,
        count: recentMessages.length,
        messages: recentMessages.slice(0, limit)
    });
});

// 3. Enviar mensaje a un número o chat
app.post('/send', async (req, res) => {
    if (!isConnected || !sock) {
        return res.status(503).json({ success: false, error: 'WhatsApp no está conectado todavía.' });
    }

    const { target, text } = req.body;
    if (!target || !text) {
        return res.status(400).json({ success: false, error: 'Faltan parámetros target o text' });
    }

    try {
        let jid = target;
        // Limpiar target si es un número telefónico (ej: +54 9 11 ... o 54911...)
        if (!jid.includes('@')) {
            const cleanNumber = target.replace(/[^0-9]/g, '');
            jid = `${cleanNumber}@s.whatsapp.net`;
        }

        await sock.sendMessage(jid, { text });
        console.log(`[Mensaje Enviado] A: ${jid} -> ${text}`);
        res.json({ success: true, target: jid, text });
    } catch (err) {
        console.error('[Error al enviar]:', err);
        res.status(500).json({ success: false, error: err.message });
    }
});

// Arrancar servicio
app.listen(PORT, '127.0.0.1', () => {
    console.log(`[Jarvis WhatsApp Service] Escuchando en http://127.0.0.1:${PORT}`);
    connectToWhatsApp();
});

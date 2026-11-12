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

// ── Almacén de contactos sincronizados desde el teléfono ──────────────────────
// { jid: { id, name, notify, verifiedName, ... } }
const contactStore = {};

function resolveTarget(target) {
    // Si ya es un JID completo
    if (target.includes('@')) return target;

    // Si parece un número telefónico (solo dígitos o con +)
    const digits = target.replace(/[^0-9]/g, '');
    if (digits.length >= 7) {
        return `${digits}@s.whatsapp.net`;
    }

    // Buscar por nombre de contacto en el store (case-insensitive, parcial)
    const nameLower = target.trim().toLowerCase();
    const match = Object.entries(contactStore).find(([jid, c]) => {
        const cname = (c.name || c.notify || c.verifiedName || '').toLowerCase();
        return cname.includes(nameLower);
    });

    if (match) return match[0]; // JID encontrado por nombre
    return null; // No se pudo resolver
}

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

    // ── Sincronización de contactos del teléfono ─────────────────────────────
    sock.ev.on('contacts.upsert', (contacts) => {
        for (const c of contacts) {
            if (c.id) {
                contactStore[c.id] = c;
            }
        }
        console.log(`[Contactos] Store actualizado: ${Object.keys(contactStore).length} contactos.`);
    });

    sock.ev.on('contacts.update', (updates) => {
        for (const c of updates) {
            if (c.id) {
                contactStore[c.id] = { ...contactStore[c.id], ...c };
            }
        }
    });

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

            // ── Filtrar broadcasts de estado (stories/status updates) ─────────
            if (from === 'status@broadcast') continue;

            // Nombre: preferir del contactStore, si no pushName del mensaje
            const storedContact = contactStore[from] || contactStore[from?.split('@')[0] + '@s.whatsapp.net'];
            const pushName = storedContact?.name || storedContact?.notify || msg.pushName || 'Desconocido';

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

// 2. Obtener los últimos mensajes recibidos (con filtro opcional por contacto)
app.get('/messages', (req, res) => {
    const limit = parseInt(req.query.limit) || 5;
    const contactFilter = req.query.contact?.toLowerCase()?.trim();

    let filtered = recentMessages;
    if (contactFilter) {
        filtered = recentMessages.filter(m =>
            m.senderName.toLowerCase().includes(contactFilter) ||
            m.from.toLowerCase().includes(contactFilter)
        );
    }

    res.json({
        connected: isConnected,
        count: filtered.length,
        messages: filtered.slice(0, limit)
    });
});

// 3. Listar contactos conocidos del store
app.get('/contacts', (req, res) => {
    const q = req.query.q?.toLowerCase()?.trim();
    let list = Object.entries(contactStore).map(([jid, c]) => ({
        jid,
        name: c.name || c.notify || c.verifiedName || jid
    }));

    if (q) {
        list = list.filter(c => c.name.toLowerCase().includes(q) || c.jid.includes(q));
    }

    res.json({ count: list.length, contacts: list });
});

// 4. Enviar mensaje a un número, nombre de contacto o JID
app.post('/send', async (req, res) => {
    if (!isConnected || !sock) {
        return res.status(503).json({ success: false, error: 'WhatsApp no está conectado todavía.' });
    }

    const { target, text } = req.body;
    if (!target || !text) {
        return res.status(400).json({ success: false, error: 'Faltan parámetros target o text' });
    }

    try {
        const jid = resolveTarget(target);

        if (!jid) {
            const msg = `Contacto "${target}" no encontrado en la lista de contactos. ` +
                `Usa el número de teléfono completo con código de país (ej: 5491112345678) ` +
                `o asegúrate que el nombre esté guardado en tu teléfono.`;
            console.warn(`[Envío fallido] ${msg}`);
            return res.status(404).json({ success: false, error: msg });
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

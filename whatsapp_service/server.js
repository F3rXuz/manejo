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
const fs = require('fs');

const app = express();
app.use(express.json());

const PORT = 3000;
let sock = null;
let qrCodeString = null;
let isConnected = false;
let userJid = null;
let defaultAreaCode = '388'; // Código de área por defecto (Jujuy / Argentina)

// ── Archivos de persistencia ──────────────────────────────────────────────────
const CONTACTS_FILE = path.join(__dirname, 'contacts.json');
const CACHE_FILE = path.join(__dirname, 'contacts_cache.json');
const MESSAGES_FILE = path.join(__dirname, 'messages_store.json');

// Stores en memoria
let contactStore = {};
let messageList = []; // Array persistente de mensajes ordenados (más nuevo al más viejo)

// ── Cargar / Guardar Contactos ────────────────────────────────────────────────
function loadStoredContacts() {
    try {
        if (fs.existsSync(CACHE_FILE)) {
            const data = fs.readFileSync(CACHE_FILE, 'utf-8');
            contactStore = JSON.parse(data);
            console.log(`[Contactos] Cargados ${Object.keys(contactStore).length} contactos desde caché.`);
        }
    } catch (e) {
        console.error('[Aviso] Error al leer contacts_cache.json:', e.message);
    }
}

function saveContactsCache() {
    try {
        fs.writeFileSync(CACHE_FILE, JSON.stringify(contactStore, null, 2), 'utf-8');
    } catch (e) {
        console.error('[Aviso] Error al guardar contacts_cache.json:', e.message);
    }
}

function loadAliases() {
    try {
        if (fs.existsSync(CONTACTS_FILE)) {
            const data = fs.readFileSync(CONTACTS_FILE, 'utf-8');
            const aliases = JSON.parse(data);
            delete aliases._comentario;
            delete aliases.ejemplo;
            return aliases;
        }
    } catch (e) {
        console.error('[Aviso] Error al leer contacts.json:', e.message);
    }
    return {};
}

function saveAlias(name, phoneOrJid) {
    try {
        let aliases = {};
        if (fs.existsSync(CONTACTS_FILE)) {
            aliases = JSON.parse(fs.readFileSync(CONTACTS_FILE, 'utf-8'));
        }
        aliases[name.toLowerCase().trim()] = phoneOrJid;
        fs.writeFileSync(CONTACTS_FILE, JSON.stringify(aliases, null, 2), 'utf-8');
        return true;
    } catch (e) {
        console.error('[Aviso] Error al guardar alias en contacts.json:', e.message);
        return false;
    }
}

// ── Cargar / Guardar Mensajes Persistentes ─────────────────────────────────────
function loadStoredMessages() {
    try {
        if (fs.existsSync(MESSAGES_FILE)) {
            const data = fs.readFileSync(MESSAGES_FILE, 'utf-8');
            messageList = JSON.parse(data);
            console.log(`[Mensajes] Cargados ${messageList.length} mensajes persistentes desde disco.`);
        }
    } catch (e) {
        console.error('[Aviso] Error al leer messages_store.json:', e.message);
    }
}

let saveMessagesTimeout = null;
function scheduleSaveMessages() {
    if (saveMessagesTimeout) return;
    saveMessagesTimeout = setTimeout(() => {
        saveMessagesTimeout = null;
        try {
            fs.writeFileSync(MESSAGES_FILE, JSON.stringify(messageList.slice(0, 1000), null, 2), 'utf-8');
        } catch (e) {
            console.error('[Aviso] Error al guardar messages_store.json:', e.message);
        }
    }, 800);
}

loadStoredContacts();
loadStoredMessages();

// ── Normalización de texto (elimina tildes, leet speak, mayúsculas y caracteres raros) ────
function normalizeText(str) {
    if (!str) return '';
    return str
        .toLowerCase()
        .replace(/€/g, 'e')
        .replace(/@/g, 'a')
        .replace(/3/g, 'e')
        .replace(/0/g, 'o')
        .replace(/1/g, 'i')
        .replace(/\$/g, 's')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '') // Quita acentos (mamá -> mama)
        .replace(/[^a-z0-9]/g, '')       // Quita espacios y signos
        .trim();
}

// ── Normalización de números telefónicos (Argentina e Internacional) ──────────
function buildPhoneCandidates(input) {
    if (!input) return [];
    let digits = input.replace(/[^0-9]/g, '');
    if (!digits) return [];

    const candidates = [];

    // Si ya empieza con 549 (formato WhatsApp Argentina)
    if (digits.startsWith('549')) {
        candidates.push(`${digits}@s.whatsapp.net`);
        return candidates;
    }

    // Si empieza con 54 (Argentina) pero sin el 9: ej. 54388... o 5411...
    if (digits.startsWith('54') && digits.length >= 11) {
        const without54 = digits.slice(2);
        const cleanWithout15 = without54.replace(/^15/, '');
        candidates.push(`549${cleanWithout15}@s.whatsapp.net`);
        candidates.push(`${digits}@s.whatsapp.net`);
        return candidates;
    }

    // Si tiene un 0 adelante (ej: 0388... o 011...)
    if (digits.startsWith('0')) {
        digits = digits.slice(1);
    }

    // Si tiene 15 al inicio (llamada celular local): ej: 154748596
    if (digits.startsWith('15') && digits.length <= 9) {
        const localNum = digits.slice(2);
        candidates.push(`549${defaultAreaCode}${localNum}@s.whatsapp.net`);
    }

    // Si es un número de 10 dígitos (ej: 3884748596 o 1123456789)
    if (digits.length === 10) {
        candidates.push(`549${digits}@s.whatsapp.net`);
        candidates.push(`${digits}@s.whatsapp.net`);
    }

    // Si es un número local de 7 u 8 dígitos (ej: 4748596)
    if (digits.length >= 6 && digits.length <= 8) {
        candidates.push(`549${defaultAreaCode}${digits}@s.whatsapp.net`);
    }

    // Si es internacional general
    candidates.push(`${digits}@s.whatsapp.net`);

    return [...new Set(candidates)];
}

// ── Verificación de existencia en WhatsApp usando onWhatsApp de Baileys ───────
async function verifyWithWhatsApp(candidateJids) {
    if (!sock || !isConnected) return candidateJids[0] || null;

    try {
        for (const candidate of candidateJids) {
            const cleanJid = candidate.includes('@') ? candidate : `${candidate}@s.whatsapp.net`;
            const results = await sock.onWhatsApp(cleanJid);
            if (results && results.length > 0 && results[0].exists) {
                return results[0].jid;
            }
        }
    } catch (e) {
        console.warn('[onWhatsApp check aviso]:', e.message);
    }

    return candidateJids[0] || null;
}

// ── Resolución inteligente de destinatario (por Nombre o por Número) ──────────
async function resolveTarget(target) {
    if (!target) return null;
    const trimmed = target.trim();

    // 1. Si ya es un JID completo
    if (trimmed.includes('@s.whatsapp.net') || trimmed.includes('@g.us') || trimmed.includes('@lid')) {
        return { jid: trimmed, name: trimmed };
    }

    // 2. Si es o parece un número telefónico
    const digitsOnly = trimmed.replace(/[^0-9]/g, '');
    if (digitsOnly.length >= 6) {
        const candidates = buildPhoneCandidates(trimmed);
        const verifiedJid = await verifyWithWhatsApp(candidates);
        if (verifiedJid) {
            return { jid: verifiedJid, name: trimmed };
        }
    }

    // 3. Buscar en el archivo de alias personalizados (contacts.json)
    const aliases = loadAliases();
    const targetNorm = normalizeText(trimmed);

    for (const [aliasName, phoneOrJid] of Object.entries(aliases)) {
        const aliasNorm = normalizeText(aliasName);
        if (aliasNorm === targetNorm || targetNorm.includes(aliasNorm) || aliasNorm.includes(targetNorm)) {
            const val = String(phoneOrJid).trim();
            if (val.includes('@s.whatsapp.net')) {
                return { jid: val, name: aliasName };
            }
            const candidates = buildPhoneCandidates(val);
            const verifiedJid = await verifyWithWhatsApp(candidates);
            if (verifiedJid) {
                return { jid: verifiedJid, name: aliasName };
            }
        }
    }

    // 4. Buscar en el almacén de contactos sincronizados
    let bestMatch = null;
    let bestMatchScore = 0; // 3: exacto, 2: empieza con, 1: contiene

    for (const [jid, contact] of Object.entries(contactStore)) {
        const namesToCheck = [
            contact.name,
            contact.notify,
            contact.verifiedName,
            contact.pushName
        ].filter(Boolean);

        for (const name of namesToCheck) {
            const norm = normalizeText(name);
            if (!norm) continue;

            if (norm === targetNorm) {
                bestMatch = { jid, name };
                bestMatchScore = 3;
                break;
            } else if (norm.startsWith(targetNorm) && bestMatchScore < 2) {
                bestMatch = { jid, name };
                bestMatchScore = 2;
            } else if (norm.includes(targetNorm) && bestMatchScore < 1) {
                bestMatch = { jid, name };
                bestMatchScore = 1;
            }
        }
        if (bestMatchScore === 3) break;
    }

    if (bestMatch) {
        return bestMatch;
    }

    // 5. Buscar en los mensajes almacenados
    for (const m of messageList) {
        const norm = normalizeText(m.senderName);
        if (norm && (norm === targetNorm || norm.includes(targetNorm) || targetNorm.includes(norm))) {
            return { jid: m.chatJid || m.senderJid, name: m.senderName };
        }
    }

    if (digitsOnly.length >= 6) {
        const candidates = buildPhoneCandidates(trimmed);
        return { jid: candidates[0], name: trimmed };
    }

    return null;
}

// ── Extracción limpia de texto de cualquier tipo de mensaje Baileys ───────────
function extractMessageText(msg) {
    if (!msg || !msg.message) return '';
    const m = msg.message;
    return (
        m.conversation ||
        m.extendedTextMessage?.text ||
        m.imageMessage?.caption ||
        m.videoMessage?.caption ||
        m.documentMessage?.caption ||
        m.buttonsResponseMessage?.selectedDisplayText ||
        m.templateButtonReplyMessage?.selectedDisplayText ||
        m.listResponseMessage?.title ||
        m.pollCreationMessage?.name ||
        (m.audioMessage ? '[Nota de voz de WhatsApp]' : null) ||
        (m.imageMessage ? '[Foto/Imagen]' : null) ||
        (m.videoMessage ? '[Video]' : null) ||
        (m.documentMessage ? `[Documento: ${m.documentMessage.fileName || 'archivo'}]` : null) ||
        (m.stickerMessage ? '[Sticker]' : null) ||
        ''
    );
}

// ── Registro de mensaje en almacén unificado (persistente) ─────────────────────
function addMessageToStore(rawMsg) {
    if (!rawMsg || !rawMsg.key) return;
    const key = rawMsg.key;
    const chatJid = key.remoteJid;
    if (!chatJid || chatJid === 'status@broadcast') return;

    const fromMe = Boolean(key.fromMe);
    const content = extractMessageText(rawMsg);
    if (!content) return;

    let senderJid = fromMe ? (userJid || 'me') : (key.participant || chatJid);
    let senderName = 'Desconocido';

    if (fromMe) {
        senderName = 'Tú';
    } else {
        const storedContact = contactStore[chatJid] || contactStore[senderJid];
        senderName = storedContact?.name || storedContact?.notify || storedContact?.verifiedName || rawMsg.pushName || 'Contacto';
    }

    let ts = Date.now();
    if (rawMsg.messageTimestamp) {
        const val = typeof rawMsg.messageTimestamp === 'number'
            ? rawMsg.messageTimestamp
            : (rawMsg.messageTimestamp.low || Date.now() / 1000);
        ts = val * 1000;
    }
    const d = new Date(ts);

    const record = {
        id: key.id || `msg_${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
        chatJid,
        senderJid,
        senderName,
        fromMe,
        text: content,
        timestamp: ts,
        time: d.toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' }),
        date: d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: 'numeric' }),
        status: rawMsg.status || (fromMe ? 2 : 1),
        isRead: Boolean(fromMe || (rawMsg.status && rawMsg.status >= 3))
    };

    const existingIdx = messageList.findIndex(m => m.id === record.id);
    if (existingIdx >= 0) {
        messageList[existingIdx] = { ...messageList[existingIdx], ...record };
    } else {
        messageList.unshift(record);
    }

    // Mantener orden descendente por timestamp
    messageList.sort((a, b) => b.timestamp - a.timestamp);

    if (messageList.length > 1000) {
        messageList.pop();
    }

    scheduleSaveMessages();
}

// ── Conexión con WhatsApp Baileys ─────────────────────────────────────────────
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
        syncFullHistory: true, // Sincronizar historial completo de chats
        browser: ['Jarvis Windows Assistant', 'Chrome', '1.0.0']
    });

    sock.ev.on('creds.update', saveCreds);

    // ── Captura de sincronización inicial completa de historial (chats, contactos y mensajes)
    sock.ev.on('messaging-history.set', ({ chats, contacts, messages }) => {
        let countContacts = 0;
        let countMessages = 0;

        if (contacts) {
            for (const c of contacts) {
                if (c.id) {
                    contactStore[c.id] = { ...(contactStore[c.id] || {}), ...c };
                    countContacts++;
                }
            }
        }
        if (chats) {
            for (const chat of chats) {
                if (chat.id && chat.name) {
                    contactStore[chat.id] = { ...(contactStore[chat.id] || {}), name: chat.name };
                    countContacts++;
                }
            }
        }
        if (messages && Array.isArray(messages)) {
            for (const m of messages) {
                addMessageToStore(m);
                countMessages++;
            }
        }

        console.log(`[Historial Sync] ${countContacts} contactos y ${countMessages} mensajes sincronizados.`);
        saveContactsCache();
    });

    // ── Sincronización continua de contactos ─────────────────────────────────
    sock.ev.on('contacts.upsert', (contacts) => {
        for (const c of contacts) {
            if (c.id) {
                contactStore[c.id] = { ...(contactStore[c.id] || {}), ...c };
            }
        }
        saveContactsCache();
    });

    sock.ev.on('contacts.update', (updates) => {
        for (const c of updates) {
            if (c.id) {
                contactStore[c.id] = { ...(contactStore[c.id] || {}), ...c };
            }
        }
        saveContactsCache();
    });

    sock.ev.on('chats.upsert', (chats) => {
        for (const chat of chats) {
            if (chat.id && chat.name) {
                contactStore[chat.id] = { ...(contactStore[chat.id] || {}), name: chat.name };
            }
        }
        saveContactsCache();
    });

    // ── Estado de conexión ───────────────────────────────────────────────────
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

            // Extraer código de área del propio número (si es 549388... -> 388)
            const myDigits = userJid.replace(/[^0-9]/g, '');
            if (myDigits.startsWith('549') && myDigits.length >= 6) {
                defaultAreaCode = myDigits.slice(3, 6);
            }

            console.log(`\n>>> [WhatsApp Service] ¡CONECTADO CON ÉXITO! <<<`);
            console.log(`Usuario: ${userJid} | Área por defecto: ${defaultAreaCode}\n`);
        }
    });

    // ── Mensajes entrantes y salientes en tiempo real ────────────────────────
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        for (const msg of messages) {
            const from = msg.key.remoteJid;
            if (from === 'status@broadcast') continue;

            // Registrar contacto en el store si trae pushName
            const pushName = msg.pushName || '';
            if (pushName && !from.includes('@g.us')) {
                contactStore[from] = {
                    ...(contactStore[from] || {}),
                    id: from,
                    name: pushName,
                    notify: pushName
                };
                saveContactsCache();
            }

            // Guardar en almacén unificado (visto o no visto, entrante o saliente)
            addMessageToStore(msg);

            const content = extractMessageText(msg);
            if (content) {
                const sName = msg.key.fromMe ? 'Tú' : (pushName || from);
                console.log(`[Mensaje] ${sName} (${from}): ${content}`);
            }
        }
    });
}

// -----------------------------------------------------------------------------
// ENDPOINTS HTTP
// -----------------------------------------------------------------------------

// 1. Estado de la conexión
app.get('/status', (req, res) => {
    res.json({
        connected: isConnected,
        hasQr: !!qrCodeString,
        user: userJid,
        defaultAreaCode,
        totalStoredMessages: messageList.length
    });
});

// 2. OBTENER MENSAJES (visto o no visto, por contacto o generales, con limit exacto)
app.get('/messages', async (req, res) => {
    const limit = parseInt(req.query.limit) || 1;
    const contactQuery = req.query.contact?.trim();
    const unreadOnly = req.query.unread === 'true';

    let list = [...messageList];

    if (unreadOnly) {
        list = list.filter(m => !m.isRead && !m.fromMe);
    }

    let resolvedContactName = null;
    let resolvedJid = null;

    if (contactQuery) {
        const resolved = await resolveTarget(contactQuery);
        if (resolved && resolved.jid) {
            resolvedJid = resolved.jid;
            resolvedContactName = resolved.name;
        }

        const queryNorm = normalizeText(contactQuery);
        const digitsQuery = contactQuery.replace(/[^0-9]/g, '');

        list = list.filter(m => {
            // Coincidencia por JID resuelto (el chat con ese contacto)
            if (resolvedJid) {
                const cleanChat = m.chatJid.split(':')[0].replace(/@.*$/, '');
                const cleanResolved = resolvedJid.split(':')[0].replace(/@.*$/, '');
                if (m.chatJid === resolvedJid || m.senderJid === resolvedJid || cleanChat === cleanResolved) {
                    return true;
                }
            }
            // Coincidencia por número
            if (digitsQuery.length >= 6) {
                if (m.chatJid.includes(digitsQuery) || m.senderJid.includes(digitsQuery)) {
                    return true;
                }
            }
            // Coincidencia por nombre de remitente normalizado
            const senderNorm = normalizeText(m.senderName);
            if (senderNorm && (senderNorm === queryNorm || senderNorm.includes(queryNorm) || queryNorm.includes(senderNorm))) {
                return true;
            }
            return false;
        });
    }

    // Ordenar de más reciente a más antiguo
    list.sort((a, b) => b.timestamp - a.timestamp);

    const resultMessages = list.slice(0, limit);

    console.log(`[Consulta /messages] Contacto: "${contactQuery || 'Todos'}" -> Encontrados: ${list.length}, Devolviendo: ${resultMessages.length}`);

    res.json({
        connected: isConnected,
        count: resultMessages.length,
        totalInChat: list.length,
        contact: resolvedContactName || contactQuery || null,
        limit,
        messages: resultMessages
    });
});

// 3. Listar todos los contactos y alias disponibles
app.get('/contacts', (req, res) => {
    const q = req.query.q ? normalizeText(req.query.q) : '';
    const aliases = loadAliases();

    const list = [];
    const seenJids = new Set();

    for (const [name, phoneOrJid] of Object.entries(aliases)) {
        list.push({ name: `${name} (alias)`, jid: phoneOrJid, type: 'alias' });
    }

    for (const [jid, c] of Object.entries(contactStore)) {
        const cname = c.name || c.notify || c.verifiedName || '';
        if (cname && !seenJids.has(jid)) {
            seenJids.add(jid);
            list.push({ name: cname, jid, type: 'sync' });
        }
    }

    let results = list;
    if (q) {
        results = list.filter(item => normalizeText(item.name).includes(q) || item.jid.includes(q));
    }

    res.json({ count: results.length, contacts: results });
});

// 4. Agregar o actualizar un contacto/alias
app.post('/contacts', async (req, res) => {
    const { name, phone } = req.body;
    if (!name || !phone) {
        return res.status(400).json({ success: false, error: 'Faltan parámetros name o phone' });
    }

    const candidates = buildPhoneCandidates(phone);
    const verifiedJid = await verifyWithWhatsApp(candidates);
    const finalJid = verifiedJid || candidates[0];

    saveAlias(name, finalJid);
    console.log(`[Contacto Guardado] "${name}" -> ${finalJid}`);

    res.json({ success: true, name, jid: finalJid });
});

// 5. ENVÍO INSTANTÁNEO DE MENSAJES
app.post('/send', async (req, res) => {
    const t0 = Date.now();
    if (!isConnected || !sock) {
        return res.status(503).json({ success: false, error: 'WhatsApp no está conectado todavía.' });
    }

    const { target, text } = req.body;
    if (!target || !text) {
        return res.status(400).json({ success: false, error: 'Faltan parámetros target o text' });
    }

    try {
        const resolved = await resolveTarget(target);

        if (!resolved || !resolved.jid) {
            const msg = `No se encontró el destinatario "${target}". Puedes indicar el número con código de área o guardarlo en contacts.json.`;
            console.warn(`[Envío fallido]: ${msg}`);
            return res.status(404).json({ success: false, error: msg });
        }

        // Envío directo al socket de Baileys
        const sentMsg = await sock.sendMessage(resolved.jid, { text });
        if (sentMsg) {
            addMessageToStore(sentMsg);
        }

        const elapsedMs = Date.now() - t0;
        console.log(`[ENVÍO INSTANTÁNEO en ${elapsedMs}ms] A: ${resolved.name} (${resolved.jid}) -> "${text}"`);

        return res.json({
            success: true,
            target: resolved.jid,
            name: resolved.name,
            text,
            elapsedMs
        });
    } catch (err) {
        console.error('[Error al enviar mensaje]:', err);
        return res.status(500).json({ success: false, error: err.message });
    }
});

// Arrancar servicio
app.listen(PORT, '127.0.0.1', () => {
    console.log(`[Jarvis WhatsApp Service] Escuchando en http://127.0.0.1:${PORT}`);
    connectToWhatsApp();
});

const express = require('express');
const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    Browsers
} = require('@whiskeysockets/baileys');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const QRCode = require('qrcode');
const { exec, execFile } = require('child_process');
const ffmpegInstaller = require('@ffmpeg-installer/ffmpeg');
const path = require('path');
const fs = require('fs');

const app = express();
app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true, limit: '50mb' }));

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
        return { jid: trimmed, allJids: [trimmed], name: trimmed };
    }

    // 2. Si es o parece un número telefónico
    const digitsOnly = trimmed.replace(/[^0-9]/g, '');
    if (digitsOnly.length >= 6) {
        const candidates = buildPhoneCandidates(trimmed);
        const verifiedJid = await verifyWithWhatsApp(candidates);
        if (verifiedJid) {
            return { jid: verifiedJid, allJids: [verifiedJid], name: trimmed };
        }
    }

    // 3. Buscar en el archivo de alias personalizados (contacts.json)
    const aliases = loadAliases();
    const targetNorm = normalizeText(trimmed);

    for (const [aliasName, phoneOrJid] of Object.entries(aliases)) {
        const aliasNorm = normalizeText(aliasName);
        if (
            aliasNorm === targetNorm ||
            targetNorm.includes(aliasNorm) ||
            aliasNorm.includes(targetNorm) ||
            targetNorm.startsWith(aliasNorm) ||
            aliasNorm.startsWith(targetNorm)
        ) {
            const rawList = Array.isArray(phoneOrJid) ? phoneOrJid : [phoneOrJid];
            const resolvedList = [];
            for (const item of rawList) {
                const s = String(item).trim();
                if (s.includes('@')) {
                    resolvedList.push(s);
                } else {
                    const cands = buildPhoneCandidates(s);
                    const v = await verifyWithWhatsApp(cands);
                    resolvedList.push(v || cands[0]);
                }
            }
            if (resolvedList.length > 0) {
                // Elegir como primary el JID que tenga la actividad más reciente en messageList
                // (para que coincida exactamente con la ventana de chat activa en el teléfono)
                let primary = null;
                for (const m of messageList) {
                    if (resolvedList.includes(m.chatJid)) {
                        primary = m.chatJid;
                        break;
                    }
                }
                if (!primary) {
                    primary = resolvedList[0];
                }
                return { jid: primary, allJids: resolvedList, name: aliasName };
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

            const isExact = norm === targetNorm;
            const isStart = norm.startsWith(targetNorm) || targetNorm.startsWith(norm);
            const isContain = norm.includes(targetNorm) || targetNorm.includes(norm);

            let score = 0;
            if (isExact) score = 3;
            else if (isStart) score = 2;
            else if (isContain) score = 1;

            if (score > bestMatchScore) {
                const all = [jid];
                if (contact.lid) all.push(contact.lid);
                if (contact.jid) all.push(contact.jid);
                if (contact.id) all.push(contact.id);
                const uniqueAll = Array.from(new Set(all));

                let primary = null;
                for (const m of messageList) {
                    if (uniqueAll.includes(m.chatJid)) {
                        primary = m.chatJid;
                        break;
                    }
                }
                if (!primary) primary = jid;

                bestMatch = { jid: primary, allJids: uniqueAll, name: contact.name || name };
                bestMatchScore = score;
            }
        }
        if (bestMatchScore === 3) break;
    }

    if (bestMatch) {
        return bestMatch;
    }

    // 5. Buscar en los mensajes almacenados
    const foundJids = new Set();
    let foundName = null;
    for (const m of messageList) {
        const norm = normalizeText(m.senderName);
        if (
            norm &&
            (norm === targetNorm ||
                norm.includes(targetNorm) ||
                targetNorm.includes(norm) ||
                norm.startsWith(targetNorm) ||
                targetNorm.startsWith(norm))
        ) {
            if (m.chatJid) foundJids.add(m.chatJid);
            if (m.senderJid) foundJids.add(m.senderJid);
            if (!foundName) foundName = m.senderName;
        }
    }
    if (foundJids.size > 0) {
        const all = Array.from(foundJids);
        let primary = null;
        for (const m of messageList) {
            if (all.includes(m.chatJid)) {
                primary = m.chatJid;
                break;
            }
        }
        if (!primary) primary = all[0];
        return { jid: primary, allJids: all, name: foundName || trimmed };
    }

    if (digitsOnly.length >= 6) {
        const candidates = buildPhoneCandidates(trimmed);
        return { jid: candidates[0], allJids: [candidates[0]], name: trimmed };
    }

    return null;
}

// ── Extracción limpia de texto de cualquier tipo de mensaje Baileys ───────────
function extractMessageText(msg) {
    if (!msg || !msg.message) return '';
    let m = msg.message;
    // Desenvolver capas (mensajes temporales, vista única, etc.)
    if (m.ephemeralMessage?.message) m = m.ephemeralMessage.message;
    if (m.viewOnceMessage?.message) m = m.viewOnceMessage.message;
    if (m.viewOnceMessageV2?.message) m = m.viewOnceMessageV2.message;
    if (m.documentWithCaptionMessage?.message) m = m.documentWithCaptionMessage.message;

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

// ── Sincronización Bajo Demanda (On-Demand Sync con el Teléfono) ──────────────
const pendingSyncWaiters = new Map();

function notifySyncFinished(chatJid) {
    if (!chatJid) return;
    const cleanJid = chatJid.split(':')[0].replace(/@.*$/, '');
    for (const [key, resolveFn] of pendingSyncWaiters.entries()) {
        const cleanKey = key.split(':')[0].replace(/@.*$/, '');
        if (cleanKey === cleanJid || key === chatJid) {
            pendingSyncWaiters.delete(key);
            resolveFn(true);
        }
    }
}

async function syncChatHistoryOnDemand(chatJid, count = 40, waitMs = 3500) {
    if (!sock || !isConnected) return false;

    console.log(`[On-Demand Sync] Pidiendo al teléfono los últimos ${count} mensajes de ${chatJid}...`);

    // Buscar el mensaje más antiguo que tengamos para ese chat
    const cleanT = chatJid.split(':')[0].replace(/@.*$/, '');
    const chatMsgs = messageList.filter(m => {
        const cleanM = (m.chatJid || '').split(':')[0].replace(/@.*$/, '');
        return m.chatJid === chatJid || cleanM === cleanT;
    });

    let oldestMsgKey = null;
    let oldestTimestamp = Date.now();

    if (chatMsgs.length > 0) {
        const oldest = chatMsgs[chatMsgs.length - 1];
        oldestMsgKey = {
            remoteJid: oldest.chatJid,
            id: oldest.id,
            fromMe: oldest.fromMe
        };
        oldestTimestamp = oldest.timestamp;
    } else {
        oldestMsgKey = {
            remoteJid: chatJid,
            id: 'INIT_SYNC_' + Date.now(),
            fromMe: false
        };
        oldestTimestamp = Date.now();
    }

    try {
        await sock.fetchMessageHistory(count, oldestMsgKey, oldestTimestamp);
    } catch (err) {
        console.warn(`[On-Demand Sync] Error al llamar fetchMessageHistory:`, err.message);
        return false;
    }

    return new Promise((resolve) => {
        const timer = setTimeout(() => {
            pendingSyncWaiters.delete(chatJid);
            resolve(false);
        }, waitMs);

        pendingSyncWaiters.set(chatJid, () => {
            clearTimeout(timer);
            resolve(true);
        });
    });
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
        shouldSyncHistoryMessage: () => true,
        browser: Browsers.ubuntu('Chrome'),
        getMessage: async (key) => {
            const found = messageList.find(m => m.id === key.id);
            if (found && found.rawMessage) return found.rawMessage;
            return { conversation: '' };
        }
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
                if (c.lid) {
                    contactStore[c.lid] = { ...(contactStore[c.lid] || {}), ...c };
                }
                if (c.jid) {
                    contactStore[c.jid] = { ...(contactStore[c.jid] || {}), ...c };
                }
            }
        }
        if (chats) {
            for (const chat of chats) {
                if (chat.id && chat.name) {
                    contactStore[chat.id] = { ...(contactStore[chat.id] || {}), id: chat.id, name: chat.name };
                    countContacts++;
                }
            }
        }
        if (messages && Array.isArray(messages)) {
            for (const m of messages) {
                addMessageToStore(m);
                countMessages++;
                if (m.key?.remoteJid) {
                    notifySyncFinished(m.key.remoteJid);
                }
            }
        }

        console.log(`\n============================================================`);
        console.log(`[Historial Sync] ¡SINCRONIZACIÓN RECIBIDA DEL TELÉFONO!`);
        console.log(`  - Contactos/Chats procesados: ${countContacts}`);
        console.log(`  - Mensajes históricos procesados: ${countMessages}`);
        console.log(`============================================================\n`);

        saveContactsCache();
        try {
            fs.writeFileSync(MESSAGES_FILE, JSON.stringify(messageList.slice(0, 5000), null, 2), 'utf-8');
        } catch (e) {}
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
            console.log('   O abre en tu navegador: http://localhost:3000/qr');
            console.log('============================================================\n');
            qrcode.generate(qr, { small: true });
            console.log('\n============================================================\n');
            try {
                QRCode.toFile(path.join(__dirname, 'qr.png'), qr, { width: 350, margin: 2 });
            } catch (e) {}
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
            notifySyncFinished(from);

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

// 0. Visualizador Web de Código QR
app.get('/qr', async (req, res) => {
    if (isConnected) {
        return res.send(`
            <html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#121b22;color:#e9edef;">
                <h2 style="color:#00a884;">¡WhatsApp ya está conectado con éxito!</h2>
                <p>Usuario: <b>${userJid}</b></p>
                <p>Ya puedes volver a la consola de Jarvis.</p>
            </body></html>
        `);
    }
    if (!qrCodeString) {
        return res.send(`
            <html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#121b22;color:#e9edef;">
                <h2>Generando código QR...</h2>
                <p>Espera un instante, se recargará automáticamente.</p>
                <script>setTimeout(() => window.location.reload(), 2000);</script>
            </body></html>
        `);
    }

    let qrDataUrl = '';
    try {
        qrDataUrl = await QRCode.toDataURL(qrCodeString, { width: 320, margin: 2 });
    } catch (e) {
        console.error('Error generando QR DataURL:', e.message);
    }

    res.send(`
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <title>Vincular WhatsApp con Jarvis</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    justify-content: center;
                    min-height: 95vh;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                    background: #111b21;
                    color: #e9edef;
                    margin: 0;
                }
                .card {
                    background: #202c33;
                    padding: 32px;
                    border-radius: 16px;
                    text-align: center;
                    max-width: 420px;
                    box-shadow: 0 12px 36px rgba(0,0,0,0.5);
                    border: 1px solid #2a3942;
                }
                h2 { margin-top: 0; color: #00a884; font-size: 24px; }
                ol {
                    text-align: left;
                    font-size: 14px;
                    color: #d1d7db;
                    line-height: 1.8;
                    margin: 16px auto;
                    padding-left: 20px;
                }
                .qr-container {
                    background: white;
                    padding: 12px;
                    border-radius: 12px;
                    margin: 20px auto;
                    display: inline-block;
                    line-height: 0;
                }
                .qr-container img {
                    width: 300px;
                    height: 300px;
                    display: block;
                }
                .footer {
                    font-size: 12px;
                    color: #8696a0;
                    margin-top: 12px;
                }
            </style>
        </head>
        <body>
            <div class="card">
                <h2>Vincular WhatsApp</h2>
                <ol>
                    <li>Abre <b>WhatsApp</b> en tu teléfono.</li>
                    <li>Toca los tres puntos o <b>Ajustes</b> &gt; <b>Dispositivos vinculados</b>.</li>
                    <li>Toca <b>Vincular un dispositivo</b> y enfoca la cámara a este código:</li>
                </ol>
                <div class="qr-container">
                    <img src="${qrDataUrl}" alt="Código QR WhatsApp" />
                </div>
                <div class="footer">Esta página se actualizará automáticamente una vez vinculado.</div>
            </div>
            <script>
                setInterval(() => {
                    fetch('/status')
                        .then(r => r.json())
                        .then(d => {
                            if (d.connected) {
                                window.location.reload();
                            }
                        })
                        .catch(() => {});
                }, 2000);
            </script>
        </body>
        </html>
    `);
});

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
        const searchJids = new Set();
        if (resolved) {
            resolvedJid = resolved.jid;
            resolvedContactName = resolved.name;
            if (resolved.allJids) {
                resolved.allJids.forEach(j => searchJids.add(j));
            }
            if (resolved.jid) searchJids.add(resolved.jid);
        }

        const queryNorm = normalizeText(contactQuery);
        const digitsQuery = contactQuery.replace(/[^0-9]/g, '');

        const doFilter = () => list.filter(m => {
            // Coincidencia por JIDs resueltos (phone JID, LID, alias)
            if (searchJids.size > 0) {
                const cleanChat = (m.chatJid || '').split(':')[0].replace(/@.*$/, '');
                const cleanSender = (m.senderJid || '').split(':')[0].replace(/@.*$/, '');
                for (const j of searchJids) {
                    const cleanJ = j.split(':')[0].replace(/@.*$/, '');
                    if (m.chatJid === j || m.senderJid === j || cleanChat === cleanJ || cleanSender === cleanJ) {
                        return true;
                    }
                }
            }
            // Coincidencia por número
            if (digitsQuery.length >= 6) {
                if ((m.chatJid || '').includes(digitsQuery) || (m.senderJid || '').includes(digitsQuery)) {
                    return true;
                }
            }
            // Coincidencia por nombre de remitente normalizado o prefijo (ej: lus <-> luisa)
            const senderNorm = normalizeText(m.senderName);
            if (
                senderNorm &&
                (senderNorm === queryNorm ||
                    senderNorm.includes(queryNorm) ||
                    queryNorm.includes(senderNorm) ||
                    senderNorm.startsWith(queryNorm) ||
                    queryNorm.startsWith(senderNorm))
            ) {
                return true;
            }
            return false;
        });

        let matches = doFilter();

        // Si tenemos pocos o ningún mensaje localmente para ese contacto, pedir al teléfono bajo demanda
        if (matches.length < limit && resolvedJid) {
            console.log(`[Consulta /messages] Pocos mensajes locales (${matches.length}/${limit}). Solicitando historial al teléfono...`);
            await syncChatHistoryOnDemand(resolvedJid, Math.max(30, limit * 2), 3500);
            list = [...messageList]; // actualizar con los mensajes recién llegados
            matches = doFilter();
        }

        list = matches;
    }

    // Ordenar de más reciente a más antiguo
    list.sort((a, b) => b.timestamp - a.timestamp);

    const resultMessages = list.slice(0, limit).map(m => {
        let sName = m.senderName;
        if (m.fromMe) {
            sName = 'Tú';
        } else if (!sName || sName === 'Contacto' || sName === 'Desconocido' || sName.toLowerCase() === 'lus') {
            const stored = contactStore[m.chatJid] || contactStore[m.senderJid];
            if (stored?.name || stored?.notify || stored?.verifiedName || stored?.pushName) {
                sName = stored.name || stored.notify || stored.verifiedName || stored.pushName;
            }
            if (resolvedContactName && (!sName || sName === 'Contacto' || sName.toLowerCase() === 'lus')) {
                sName = resolvedContactName.charAt(0).toUpperCase() + resolvedContactName.slice(1);
            }
        }
        return { ...m, senderName: sName };
    });

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

// 5.1 ENVÍO DE MENSAJES DE VOZ / AUDIO (PTT con Opus nativo)
app.post('/send-voice', async (req, res) => {
    const t0 = Date.now();
    if (!isConnected || !sock) {
        return res.status(503).json({ success: false, error: 'WhatsApp no está conectado todavía.' });
    }

    const { target, text, audioPath, audioBase64 } = req.body;
    if (!target) {
        return res.status(400).json({ success: false, error: 'Falta el parámetro target' });
    }
    if (!text && !audioPath && !audioBase64) {
        return res.status(400).json({ success: false, error: 'Falta el texto o el archivo de audio' });
    }

    try {
        const resolved = await resolveTarget(target);
        if (!resolved || !resolved.jid) {
            return res.status(404).json({ success: false, error: `No se encontró el destinatario "${target}"` });
        }

        const voiceId = `voice_${Date.now()}_${Math.random().toString(36).substring(2, 6)}`;
        let inputAudio = audioPath;
        let isTempInput = false;

        // Si no se proporcionó archivo de audio pero sí texto, generarlo con edge_tts
        if (!inputAudio && !audioBase64 && text) {
            inputAudio = path.join(__dirname, `${voiceId}.mp3`);
            isTempInput = true;
            await new Promise((resolve, reject) => {
                const safeText = text.replace(/"/g, '\\"');
                const pyCmd = `python -m edge_tts --text "${safeText}" --voice es-AR-ElenaNeural --write-media "${inputAudio}"`;
                exec(pyCmd, (err) => {
                    if (err) reject(err);
                    else resolve();
                });
            });
        } else if (audioBase64) {
            inputAudio = path.join(__dirname, `${voiceId}.mp3`);
            isTempInput = true;
            fs.writeFileSync(inputAudio, Buffer.from(audioBase64, 'base64'));
        }

        if (!fs.existsSync(inputAudio)) {
            return res.status(400).json({ success: false, error: `El archivo de audio no existe: ${inputAudio}` });
        }

        // Convertir a Opus OGG para WhatsApp PTT oficial con waveform nativo
        let finalOgg = path.join(__dirname, `${voiceId}.ogg`);
        let sendBuffer = null;
        let usedMime = 'audio/ogg; codecs=opus';

        try {
            await new Promise((resolve, reject) => {
                execFile(ffmpegInstaller.path, [
                    '-y',
                    '-i', inputAudio,
                    '-c:a', 'libopus',
                    '-b:a', '32k',
                    '-vbr', 'on',
                    finalOgg
                ], (err) => {
                    if (err) reject(err);
                    else resolve();
                });
            });
            sendBuffer = fs.readFileSync(finalOgg);
        } catch (convErr) {
            console.warn('[Aviso] Falló conversión a OGG Opus, enviando audio original:', convErr.message);
            sendBuffer = fs.readFileSync(inputAudio);
            usedMime = 'audio/mp4';
        }

        // Generar forma de onda WhatsApp (64 barras de amplitud) para que la UI renderice el reproductor completo
        const waveform = new Uint8Array(64);
        for (let i = 0; i < 64; i++) {
            const val = Math.floor(15 + 80 * Math.abs(Math.sin((i / 64) * Math.PI * 3.5) * Math.cos(i * 0.35)));
            waveform[i] = Math.min(100, Math.max(8, val));
        }

        // Enviar como PTT (Push-to-Talk = Mensaje de Voz oficial de WhatsApp)
        const sentMsg = await sock.sendMessage(resolved.jid, {
            audio: sendBuffer,
            mimetype: usedMime,
            ptt: true,
            waveform
        });

        // Limpiar archivos temporales
        try {
            if (isTempInput && fs.existsSync(inputAudio)) fs.unlinkSync(inputAudio);
            if (fs.existsSync(finalOgg)) fs.unlinkSync(finalOgg);
        } catch (e) {}

        if (sentMsg) {
            const displayText = text ? `[Nota de voz: "${text}"]` : '[Nota de voz de WhatsApp]';
            sentMsg.message = sentMsg.message || {};
            sentMsg.message.audioMessage = sentMsg.message.audioMessage || {};
            sentMsg.message.conversation = displayText;
            addMessageToStore(sentMsg);
        }

        const elapsedMs = Date.now() - t0;
        console.log(`[NOTA DE VOZ ENVIADA en ${elapsedMs}ms] A: ${resolved.name} (${resolved.jid}) -> "${text || 'audio'}"`);

        return res.json({
            success: true,
            target: resolved.jid,
            name: resolved.name,
            text,
            isVoiceNote: true,
            elapsedMs
        });
    } catch (err) {
        console.error('[Error al enviar nota de voz]:', err);
        return res.status(500).json({ success: false, error: err.message });
    }
});

// 6. SINCRONIZAR HISTORIAL DE CHAT BAJO DEMANDA (ON-DEMAND SYNC)
app.post('/sync', async (req, res) => {
    const { contact, target, count } = req.body;
    const toSync = contact || target;
    if (!toSync) {
        return res.status(400).json({ success: false, error: 'Falta parámetro contact o target' });
    }

    const resolved = await resolveTarget(toSync);
    if (!resolved || !resolved.jid) {
        return res.status(404).json({ success: false, error: `No se encontró el contacto ${toSync}` });
    }

    const howMany = parseInt(count) || 40;
    const ok = await syncChatHistoryOnDemand(resolved.jid, howMany, 4500);
    const updatedCount = messageList.filter(m => {
        const cleanChat = (m.chatJid || '').split(':')[0].replace(/@.*$/, '');
        const cleanResolved = resolved.jid.split(':')[0].replace(/@.*$/, '');
        return m.chatJid === resolved.jid || cleanChat === cleanResolved;
    }).length;

    res.json({
        success: true,
        contact: resolved.name,
        jid: resolved.jid,
        synced: ok,
        totalMessagesInStore: updatedCount
    });
});

// Arrancar servicio
app.listen(PORT, '127.0.0.1', () => {
    console.log(`[Jarvis WhatsApp Service] Escuchando en http://127.0.0.1:${PORT}`);
    connectToWhatsApp();
});

// Read-only bridge for the user's existing Electron safeStorage credential.
// stdout is a private pipe consumed by model_client; never invoke in a terminal.
const { app, safeStorage } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

app.disableHardwareAcceleration();
const original = path.resolve(process.argv[2] || '');
const expected = path.resolve(process.env.APPDATA || '', 'Desktop Pet');
if (original !== expected) process.exit(2);
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'bank-model-key-'));
const state = JSON.parse(fs.readFileSync(path.join(original, 'Local State'), 'utf8'));
// Only the DPAPI-encrypted Chromium key is needed, never desktop history.
fs.writeFileSync(path.join(temporary, 'Local State'), JSON.stringify({ os_crypt: state.os_crypt }));
app.setPath('userData', temporary);
app.whenReady().then(() => {
  try {
    const stored = JSON.parse(fs.readFileSync(path.join(original, 'deepseek-credentials.json'), 'utf8'));
    if (stored.version !== 1 || stored.provider !== 'DeepSeek' || typeof stored.encryptedKey !== 'string' || !safeStorage.isEncryptionAvailable()) throw new Error();
    const key = safeStorage.decryptString(Buffer.from(stored.encryptedKey, 'base64'));
    if (key.length < 12 || key.length > 512 || /\s/.test(key)) throw new Error();
    process.stdout.write(JSON.stringify({ key }));
    app.exit(0);
  } catch { app.exit(2); }
});
process.on('exit', () => {
  // The path is the exact directory created above, under the fixed temp parent.
  if (path.dirname(temporary) === path.resolve(os.tmpdir()) && path.basename(temporary).startsWith('bank-model-key-')) {
    try { fs.rmSync(temporary, { recursive: true, force: true }); } catch {}
  }
});

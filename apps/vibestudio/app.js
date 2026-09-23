'use strict';
const $ = id => document.getElementById(id);
const bytes = value => new TextEncoder().encode(value).length;
let suggestion = '';
function status(message, error = false) { $('status').textContent = message; $('status').classList.toggle('error', error); }
async function refresh() {
  try {
    const res = await fetch('/api/files'); const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    $('files').replaceChildren();
    for (const name of data.files) {
      const label = document.createElement('label'); const box = document.createElement('input');
      box.type = 'checkbox'; box.value = name;
      const text = document.createElement('span'); text.textContent = name;
      label.append(box, text); $('files').append(label);
    }
    if (!data.files.length) $('files').textContent = 'No eligible files in the workspace. Attach a small source file or use a prompt alone.';
  } catch (err) { $('files').textContent = 'Workspace unavailable: ' + err.message; }
}
$('refresh').addEventListener('click', refresh);
$('prompt').addEventListener('input', () => { $('budget').textContent = `Prompt: ${bytes($('prompt').value)} / 1,200 bytes`; });
$('upload').addEventListener('change', () => { $('upload-summary').textContent = Array.from($('upload').files).map(f => f.name).join(', ') || 'Attachments stay in memory, not on disk.'; });
$('form').addEventListener('submit', async event => {
  event.preventDefault();
  $('generate').disabled = true; $('copy').disabled = true; $('download').disabled = true;
  suggestion = ''; $('output').textContent = '';
  try {
    const files = Array.from($('files').querySelectorAll('input:checked')).map(x => x.value);
    const attached = Array.from($('upload').files);
    if (files.length + attached.length > 8) throw new Error('Select at most 8 files including attachments.');
    if (bytes($('prompt').value) > 1200) throw new Error('Prompt exceeds 1,200 UTF-8 bytes.');
    const uploads = [];
    for (const file of attached) {
      if (file.size > 2048) throw new Error(`${file.name}: file exceeds 2,048 bytes.`);
      const content = new TextDecoder('utf-8', {fatal:true}).decode(await file.arrayBuffer());
      uploads.push({name:file.name, content});
    }
    status('Waiting for local Ollama. CPU generation may take up to 10 minutes…');
    const response = await fetch('/api/generate', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({prompt:$('prompt').value, files, uploads})});
    if (!response.ok) { const data = await response.json(); throw new Error(data.error || 'Request failed.'); }
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let done = false;
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, {stream:true});
        let index;
        while ((index = buffer.indexOf('\n')) !== -1) {
          const record = JSON.parse(buffer.slice(0, index)); buffer = buffer.slice(index + 1);
          if (record.type === 'error') throw new Error(record.error);
          if (record.type === 'delta') { suggestion += record.text; $('output').textContent = suggestion; status('Receiving suggestion from local Ollama…'); }
          if (record.type === 'done') done = true;
        }
      }
      if (!done) throw new Error('Connection ended before completion. Any text shown is incomplete.');
      status('Suggestion complete. Review and test it yourself before use.');
    } finally { await reader.cancel(); }
  } catch (err) { status(err.message + (suggestion ? ' Partial output retained below.' : ''), true); }
  finally { $('generate').disabled = false; $('copy').disabled = !suggestion; $('download').disabled = !suggestion; }
});
$('copy').addEventListener('click', async () => {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(suggestion);
    else {
      const field = document.createElement('textarea'); field.value = suggestion; document.body.append(field); field.select();
      const copied = document.execCommand('copy'); field.remove(); if (!copied) throw new Error('Copy unavailable');
    }
    status('Copied suggestion.');
  } catch (_) { status('Browser blocked clipboard access. Select the result or use Download.', true); }
});
$('download').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([suggestion], {type:'text/plain;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = 'vibestudio-suggestion.txt'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
refresh();

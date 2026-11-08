// UI for the same Node-RED workflow used by live speech and camera recognition.
(() => {
  const element = id => document.getElementById(id);
  let connected = false;
  let timer = null;
  let lastSequence = 0;
  let saving = false;
  const conversation = crypto.randomUUID();

  async function request(path, body) {
    const headers = {Authorization: 'Bearer ' + element('code').value.trim()};
    const options = {headers};
    if (body !== undefined) {
      options.method = 'POST';
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  function speak(text) {
    if (!element('read-replies').checked || !('speechSynthesis' in window)) return;
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = navigator.language;
    utterance.onstart = () => window.dispatchEvent(new CustomEvent('ioe-speaking', {detail: true}));
    const resume = () => window.dispatchEvent(new CustomEvent('ioe-speaking', {detail: false}));
    utterance.onend = utterance.onerror = resume;
    speechSynthesis.speak(utterance);
  }

  function render(data, initial) {
    element('system-controls').hidden = false;
    element('system-status').textContent = 'Connected · simulated devices';
    element('service-status').textContent = `${data.health.node_red ? 'Node-RED ready' : 'Node-RED unavailable'} · ${data.health.ollama ? 'Ollama ready' : 'Ollama/model unavailable'} · ${data.health.model}`;
    for (const name of ['lights', 'pump']) {
      const device = data.devices[name];
      const confirmed = device.confirmed === null ? 'unconfirmed' : device.confirmed ? 'on' : 'off';
      element(name + '-state').textContent = `Confirmed ${confirmed} · ${device.status}`;
      element(name + '-message').textContent = device.message;
      const select = element(name + '-outcome');
      if (!saving && document.activeElement !== select) select.value = data.mocks[name].outcome;
    }
    if (!saving) {
      element('voice-commands').checked = data.recognition.voice_enabled;
      element('gesture-commands').checked = data.recognition.gesture_enabled;
    }
    element('recognition-status').textContent = data.voice_status + (data.last_gesture ? ' · ' + data.last_gesture : '');
    element('transcript').textContent = data.last_transcript ? 'Heard: ' + data.last_transcript : '';
    const log = element('system-events');
    log.replaceChildren();
    for (const event of data.events.slice(-12)) {
      const item = document.createElement('li');
      item.className = event.kind;
      item.textContent = `${event.kind} · ${event.message}`;
      log.appendChild(item);
    }
    const replies = data.events.filter(event => ['success', 'warning', 'error', 'timeout', 'clarify', 'status', 'reply'].includes(event.kind));
    const latest = replies.at(-1);
    if (latest) {
      element('assistant-reply').textContent = latest.message;
      if (!initial && latest.sequence > lastSequence) speak(latest.message);
    }
    lastSequence = data.events.at(-1)?.sequence || lastSequence;
  }

  async function poll(initial = false) {
    clearTimeout(timer);
    try {
      render(await request('/api/system'), initial);
    } catch (error) {
      element('system-status').textContent = error.message;
    }
    if (connected) timer = setTimeout(() => poll(), 750);
  }

  async function connect() {
    if (!/^[0-9]{6}$/.test(element('code').value.trim())) {
      element('system-status').textContent = 'Enter the 6-digit pairing code above.';
      return;
    }
    connected = true;
    await poll(true);
  }
  element('connect-system').onclick = connect;
  window.addEventListener('ioe-connected', connect);

  async function send(source, text, button) {
    if (!text.trim()) return;
    button.disabled = true;
    element('assistant-reply').textContent = 'Processing through Node-RED…';
    try {
      const result = await request('/api/input', {request_id: crypto.randomUUID(), conversation_id: conversation, source, text, confidence: 1.0});
      element('assistant-reply').textContent = result.reply;
      await poll();
    } catch (error) {
      element('assistant-reply').textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }
  element('send-command').onclick = () => send('text', element('command-text').value, element('send-command'));
  element('send-gesture').onclick = () => send('gesture', element('test-gesture').value, element('send-gesture'));
  for (const name of ['lights', 'pump']) {
    element(name + '-outcome').onchange = async () => {
      saving = true;
      try {
        await request('/api/mock', {device: name, outcome: element(name + '-outcome').value, delay_ms: 500});
      } catch (error) {
        element('system-status').textContent = error.message;
      } finally {
        saving = false;
        await poll();
      }
    };
  }
  async function configureRecognition() {
    saving = true;
    try {
      await request('/api/recognition', {voice_enabled: element('voice-commands').checked, gesture_enabled: element('gesture-commands').checked});
    } catch (error) {
      element('system-status').textContent = error.message;
    } finally {
      saving = false;
      await poll();
    }
  }
  element('voice-commands').onchange = element('gesture-commands').onchange = configureRecognition;
  element('read-replies').onchange = () => {
    if (!element('read-replies').checked && 'speechSynthesis' in window) {
      speechSynthesis.cancel();
      window.dispatchEvent(new CustomEvent('ioe-speaking', {detail: false}));
    }
  };
  window.addEventListener('pagehide', () => { connected = false; clearTimeout(timer); });
})();

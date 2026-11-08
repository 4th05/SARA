// The phone and monitor use the same real recognition/Node-RED workflow.
(() => {
  const element = id => document.getElementById(id);
  const devices = {bathroom: 'Bathroom light', kitchen: 'Kitchen light', bedroom: 'Bedroom light', pump: 'Water pump'};
  const player = element('reply-audio');
  const conversation = crypto.randomUUID();
  let connected = false;
  let polling = false;
  let timer = null;
  let lastSequence = 0;
  let saving = false;
  let speechQueue = [];
  let playing = false;
  let playbackGeneration = 0;
  let audioURL = null;
  let primed = false;

  if (new URLSearchParams(location.search).get('view') === 'monitor') element('read-replies').checked = false;
  for (const [name, label] of Object.entries(devices)) {
    const card = document.createElement('div');
    card.className = 'device-card';
    card.id = name + '-card';
    card.innerHTML = `<h3>${label}</h3><p id="${name}-state" class="light-state">Unconfirmed</p>
      <p id="${name}-message" class="note"></p><label for="${name}-outcome">Controller feedback</label>
      <select id="${name}-outcome"><option value="success">Success · apply command</option>
      <option value="warning">Warning · apply with warning</option><option value="error">Error · reject command</option>
      <option value="timeout">Timeout · no confirmation</option></select>`;
    element('device-cards').appendChild(card);
  }

  async function request(path, body) {
    const headers = {Authorization: 'Bearer ' + element('code').value.trim()};
    const options = {headers};
    if (body !== undefined) {
      options.method = 'POST';
      headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    if (!response.ok) {
      const error = new Error(await response.text());
      error.status = response.status;
      throw error;
    }
    return response;
  }
  async function json(path, body) { return (await request(path, body)).json(); }
  const speaking = active => window.dispatchEvent(new CustomEvent('ioe-speaking', {detail: active}));

  function primeAudio() {
    if (primed || !element('read-replies').checked) return;
    primed = true;
    // Unlock this audio element during the user's initial tap, including on mobile Safari.
    const bytes = new Uint8Array(46);
    const view = new DataView(bytes.buffer);
    const write = (offset, text) => [...text].forEach((letter, index) => view.setUint8(offset + index, letter.charCodeAt(0)));
    write(0, 'RIFF'); view.setUint32(4, 38, true); write(8, 'WAVE'); write(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 22050, true); view.setUint32(28, 44100, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, 2, true);
    const silence = URL.createObjectURL(new Blob([bytes], {type: 'audio/wav'}));
    player.src = silence;
    player.play().catch(() => {}).finally(() => URL.revokeObjectURL(silence));
  }

  async function playNext() {
    if (playing || !speechQueue.length || !element('read-replies').checked) return;
    const event = speechQueue.shift();
    const generation = playbackGeneration;
    playing = true;
    element('speech-status').textContent = 'Generating voice locally…';
    element('spoken-text').textContent = event.message;
    try {
      const response = await request(`/api/speech/${event.sequence}.wav`);
      const blob = await response.blob();
      if (generation !== playbackGeneration) return;
      if (audioURL) URL.revokeObjectURL(audioURL);
      audioURL = URL.createObjectURL(blob);
      player.src = audioURL;
      try {
        await player.play();
      } catch (error) {
        speaking(false);
        element('speech-status').textContent = 'Tap Play in the audio player to hear this reply.';
      }
    } catch (error) {
      if (generation !== playbackGeneration) return;
      playing = false;
      speaking(false);
      element('speech-status').textContent = 'Voice playback failed: ' + error.message;
    }
  }
  player.onplay = () => {
    speaking(true);
    element('speech-status').textContent = 'Speaking · microphone paused on this device';
  };
  player.onpause = () => speaking(false);
  player.onended = () => {
    speaking(false);
    playing = false;
    element('speech-status').textContent = 'Reply finished. You can speak again or replay the audio.';
    playNext();
  };
  player.onerror = () => {
    speaking(false);
    playing = false;
    element('speech-status').textContent = 'Audio could not play. Retry with Test voice.';
  };

  function render(data, initial) {
    element('system-controls').hidden = false;
    element('system-status').textContent = 'Connected · real voice input · simulated Arduino';
    element('service-status').textContent = `${data.health.node_red ? 'Node-RED ready' : 'Node-RED unavailable'} · ${data.health.ollama ? 'Ollama ready' : 'Ollama/model unavailable'} · ${data.speech_output.available ? data.speech_output.backend : 'Speech engine missing'}`;
    for (const name of Object.keys(devices)) {
      const device = data.devices[name];
      const confirmed = device.confirmed === null ? 'UNCONFIRMED' : device.confirmed ? 'ON' : 'OFF';
      element(name + '-state').textContent = `${confirmed} · ${device.status}`;
      element(name + '-card').classList.toggle('on', device.confirmed === true);
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
    const replies = data.events.filter(event => event.speak);
    const latest = replies.at(-1);
    if (latest && (initial || latest.sequence > lastSequence)) element('assistant-reply').textContent = latest.message;
    if (!initial && element('read-replies').checked) {
      speechQueue.push(...replies.filter(event => event.sequence > lastSequence));
      playNext();
    }
    lastSequence = data.events.at(-1)?.sequence || lastSequence;
  }

  async function poll(initial = false) {
    if (polling) return;
    polling = true;
    clearTimeout(timer);
    try {
      render(await json('/api/system'), initial);
    } catch (error) {
      element('system-status').textContent = error.message;
      if (error.status === 401 || error.status === 429) {
        connected = false;
        element('system-status').textContent += ' Enter the current terminal code and reconnect.';
      }
    } finally {
      polling = false;
      if (connected) timer = setTimeout(() => poll(), 500);
    }
  }
  async function connect() {
    if (!/^[0-9]{6}$/.test(element('code').value.trim())) {
      element('system-status').textContent = 'Enter the 6-digit pairing code above.';
      return;
    }
    const initial = !connected;
    connected = true;
    await poll(initial);
  }
  element('connect-system').onclick = () => { primeAudio(); connect(); };
  element('start').addEventListener('click', primeAudio);
  window.addEventListener('ioe-connected', connect);

  async function send(source, text, button) {
    if (!text.trim()) return;
    button.disabled = true;
    primeAudio();
    element('assistant-reply').textContent = 'Interpreting through Ollama…';
    try {
      const result = await json('/api/input', {request_id: crypto.randomUUID(), conversation_id: conversation, source, text, confidence: 1.0});
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
  for (const name of Object.keys(devices)) {
    element(name + '-outcome').onchange = async () => {
      saving = true;
      try {
        await json('/api/mock', {device: name, outcome: element(name + '-outcome').value, delay_ms: 500});
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
      await json('/api/recognition', {voice_enabled: element('voice-commands').checked, gesture_enabled: element('gesture-commands').checked});
    } catch (error) {
      element('system-status').textContent = error.message;
    } finally {
      saving = false;
      await poll();
    }
  }
  element('voice-commands').onchange = element('gesture-commands').onchange = configureRecognition;
  element('read-replies').onchange = () => {
    if (element('read-replies').checked) {
      primeAudio();
    } else {
      playbackGeneration++;
      speechQueue = [];
      player.pause();
      playing = false;
      speaking(false);
      element('speech-status').textContent = 'Automatic voice replies disabled. The last reply can still be played manually.';
    }
  };
  element('test-voice').onclick = async () => {
    element('read-replies').checked = true;
    primeAudio();
    try { await json('/api/speech/test', {}); await poll(); }
    catch (error) { element('speech-status').textContent = error.message; }
  };
  window.addEventListener('pagehide', () => {
    connected = false;
    clearTimeout(timer);
    speechQueue = [];
    playbackGeneration++;
    player.pause();
    if (audioURL) URL.revokeObjectURL(audioURL);
  });
})();

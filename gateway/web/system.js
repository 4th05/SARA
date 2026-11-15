(() => {
  const element = id => document.getElementById(id);
  const rooms = {bathroom: 'Bathroom', kitchen: 'Kitchen', bedroom: 'Bedroom'};
  const hold = element('hold');
  const player = element('reply-audio');
  let held = false;
  let capture = null;
  let closing = false;
  let waiting = false;
  let serverBusy = false;
  let speaking = false;
  let admin = false;
  let timer = null;
  let captureTimer = null;
  let lastSequence = null;
  let lastReply = null;
  let speechQueue = [];
  let playing = false;
  let audioURL = null;
  let previewURL = null;
  let audioUnlocked = false;

  async function request(path, body, options = {}) {
    const init = {credentials: 'same-origin', ...options};
    if (body !== undefined) {
      init.method = options.method || 'POST';
      if (body instanceof Blob) {
        init.body = body;
        init.headers = {'Content-Type': body.type};
      } else {
        init.body = JSON.stringify(body);
        init.headers = {'Content-Type': 'application/json'};
      }
    }
    const response = await fetch(path, init);
    if (!response.ok) {
      const error = new Error(await response.text());
      error.status = response.status;
      throw error;
    }
    return response;
  }
  async function json(path, body, options) { return (await request(path, body, options)).json(); }
  const mode = () => document.querySelector('input[name=mode]:checked').value;
  const label = () => mode() === 'audio' ? 'Hold to speak' : mode() === 'video' ? 'Hold for video' : 'Hold to speak & record';
  const status = text => { element('capture-status').textContent = text; };

  function updateControls() {
    hold.textContent = held ? 'Release to send' : label();
    hold.setAttribute('aria-label', hold.textContent);
    hold.classList.toggle('active', held);
    hold.disabled = !held && (closing || waiting || serverBusy || playing);
    for (const input of document.querySelectorAll('input[name=mode]')) input.disabled = held || closing || !!capture;
    element('video-note').hidden = mode() === 'audio';
  }
  document.querySelectorAll('input[name=mode]').forEach(input => input.onchange = updateControls);

  for (const [room, name] of Object.entries(rooms)) {
    const card = document.createElement('div');
    card.id = room + '-state';
    card.className = 'light';
    card.innerHTML = `<strong>${name}</strong><span>Unconfirmed</span>`;
    element('lights').appendChild(card);
    const configuration = document.createElement('div');
    configuration.className = 'admin-device';
    configuration.innerHTML = `<label for="${room}-outcome">${name}</label>
      <select id="${room}-outcome"><option value="success">Success</option><option value="warning">Warning</option>
      <option value="error">Error</option><option value="timeout">Timeout</option></select><p id="${room}-detail"></p>`;
    element('admin-devices').appendChild(configuration);
    element(room + '-outcome').onchange = async event => {
      try { await json('/api/mock', {device: room, outcome: event.target.value, delay_ms: 500}); }
      catch (error) { element('admin-health').textContent = error.message; }
    };
  }

  function unlockAudio() {
    if (audioUnlocked) return;
    audioUnlocked = true;
    // Prime this player during the user's gesture for mobile browser playback permissions.
    const bytes = new Uint8Array(46);
    const view = new DataView(bytes.buffer);
    const write = (offset, text) => [...text].forEach((letter, index) => view.setUint8(offset + index, letter.charCodeAt(0)));
    write(0, 'RIFF'); view.setUint32(4, 38, true); write(8, 'WAVE'); write(12, 'fmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 22050, true); view.setUint32(28, 44100, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, 2, true);
    const url = URL.createObjectURL(new Blob([bytes], {type: 'audio/wav'}));
    player.src = url;
    player.play().catch(() => {}).finally(() => URL.revokeObjectURL(url));
  }

  async function playNext() {
    if (playing || !speechQueue.length || held || capture) return;
    playing = true;
    const reply = speechQueue.shift();
    lastReply = reply;
    element('replay').hidden = false;
    element('audio-status').textContent = 'Preparing spoken reply…';
    updateControls();
    try {
      const response = await request(`/api/speech/${reply.sequence}.wav`);
      const blob = await response.blob();
      if (audioURL) URL.revokeObjectURL(audioURL);
      audioURL = URL.createObjectURL(blob);
      player.src = audioURL;
      try { await player.play(); }
      catch {
        element('audio-status').textContent = 'Tap Play to hear the reply.';
        status('Reply ready. Tap Play to listen.');
      }
    } catch (error) {
      playing = false;
      element('audio-status').textContent = 'Voice unavailable. Tap Replay last reply to retry.';
      status(error.message);
      updateControls();
    }
  }
  player.onplay = () => {
    if (!audioURL) return;
    speaking = true;
    element('audio-status').textContent = 'Speaking…';
    updateControls();
  };
  player.onpause = () => { speaking = false; };
  player.onended = () => {
    if (!audioURL) return;
    speaking = false;
    playing = false;
    element('audio-status').textContent = 'Reply finished';
    if (!waiting && !serverBusy && !held) status('Ready');
    updateControls();
    playNext();
  };
  player.onerror = () => {
    if (!audioURL) return;
    speaking = false;
    playing = false;
    element('audio-status').textContent = 'Audio playback failed. Tap Replay last reply.';
    updateControls();
  };
  element('replay').onclick = () => {
    unlockAudio();
    if (lastReply && !playing) { speechQueue.push(lastReply); playNext(); }
    else if (player.paused && player.src) player.play().catch(() => {});
  };

  async function publish(captured) {
    const peer = new RTCPeerConnection({iceServers: []});
    captured.peer = peer;
    captured.stream.getTracks().forEach(track => peer.addTrack(track, captured.stream));
    peer.getTransceivers().forEach(transceiver => { transceiver.direction = 'sendonly'; });
    await peer.setLocalDescription(await peer.createOffer());
    if (peer.iceGatheringState !== 'complete') {
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('Media connection timed out.')), 10000);
        peer.addEventListener('signalingstatechange', () => {
          if (peer.signalingState === 'closed') { clearTimeout(timeout); resolve(); }
        });
        peer.addEventListener('icegatheringstatechange', () => {
          if (peer.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); }
        });
        if (peer.iceGatheringState === 'complete') { clearTimeout(timeout); resolve(); }
      });
    }
    if (captured.stopped) return;
    const answer = await json('/api/offer', {type: 'offer', sdp: peer.localDescription.sdp});
    captured.session = answer.session;
    if (!captured.stopped) await peer.setRemoteDescription({type: answer.type, sdp: answer.sdp});
  }

  async function beginCapture() {
    if (capture || closing || waiting || serverBusy || playing || admin) return;
    held = true;
    unlockAudio();
    const selected = mode();
    const captured = {audio: selected !== 'video', video: selected !== 'audio', stopped: false};
    capture = captured;
    updateControls();
    status('Opening media…');
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw Error('Open the phone HTTPS address printed by the gateway.');
      const mediaRequest = navigator.mediaDevices.getUserMedia({
        audio: captured.audio ? {echoCancellation: true, noiseSuppression: true} : false,
        video: captured.video ? {facingMode: 'user', width: {ideal: 640}, height: {ideal: 480}, frameRate: {ideal: 15}} : false
      });
      mediaRequest.then(stream => {
        if (captured.stopped) stream.getTracks().forEach(track => track.stop());
      }).catch(() => {});
      let permissionTimeout;
      captured.opening = Promise.race([mediaRequest, new Promise((resolve, reject) => {
        permissionTimeout = setTimeout(() => reject(new Error('Media access timed out. Check microphone/camera permission in your browser and device settings.')), 15000);
      })]);
      let stream;
      try { stream = await captured.opening; }
      finally { clearTimeout(permissionTimeout); }
      captured.stream = stream;
      if (!held || captured.stopped) {
        stream.getTracks().forEach(track => track.stop());
        if (!held) status('Ready. Hold again to record.');
        return;
      }
      if (captured.audio) {
        if (!window.MediaRecorder) throw Error('This browser cannot record audio. Use a browser with MediaRecorder support.');
        const types = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'];
        const type = types.find(value => MediaRecorder.isTypeSupported(value));
        const recorder = new MediaRecorder(new MediaStream(stream.getAudioTracks()), type ? {mimeType: type} : {});
        const chunks = [];
        captured.recorder = recorder;
        captured.recording = new Promise((resolve, reject) => {
          recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
          recorder.onstop = () => resolve(new Blob(chunks, {type: recorder.mimeType}));
          recorder.onerror = () => reject(new Error('Audio recording failed. Please try again.'));
        });
        recorder.start(100);
      }
      if (captured.video) {
        element('local-preview').srcObject = stream;
        element('local-preview').hidden = false;
      }
      captured.publishing = publish(captured).catch(error => { captured.publishError = error; });
      status('Recording… release to send. Maximum 15 seconds.');
      captureTimer = setTimeout(releaseCapture, 15000);
    } catch (error) {
      captured.error = error;
      status(error.name === 'NotAllowedError' ? 'Media permission was denied. Allow microphone/camera access and hold again.' : error.message);
      held = false;
      await finishCapture(captured);
    }
  }

  async function finishCapture(captured) {
    if (captured.finishing) return;
    captured.finishing = true;
    captured.stopped = true;
    closing = true;
    clearTimeout(captureTimer);
    if (captured.recorder?.state === 'recording') captured.recorder.stop();
    captured.stream?.getTracks().forEach(track => track.stop());
    captured.peer?.close();
    element('local-preview').srcObject = null;
    element('local-preview').hidden = true;
    updateControls();
    try {
      if (!captured.stream && captured.opening) {
        status('Waiting for media permission. Allow access, then hold again.');
        await captured.opening;
      }
      if (captured.recording && !captured.error) {
        waiting = true;
        status('Processing your request…');
        const audio = await captured.recording;
        await request('/api/recording', audio);
      }
      await captured.publishing;
      if (captured.session) {
        await json('/api/session', {session: captured.session}, {method: 'DELETE'});
      }
      if (!captured.audio && captured.stream && !captured.error) {
        if (captured.publishError) throw captured.publishError;
        waiting = true;
        await json('/api/video/complete', {});
      }
      if (!captured.recording && captured.audio && !captured.error) status('Ready. Hold again to record.');
    } catch (error) {
      waiting = false;
      status(error.message);
    } finally {
      captured.peer?.close();
      if (capture === captured) capture = null;
      closing = false;
      updateControls();
      playNext();
    }
  }

  async function releaseCapture() {
    if (!held) return;
    held = false;
    if (capture) await finishCapture(capture);
    updateControls();
  }
  hold.onpointerdown = event => {
    if (event.button !== 0) return;
    event.preventDefault();
    hold.setPointerCapture(event.pointerId);
    beginCapture();
  };
  hold.onpointerup = hold.onpointercancel = hold.onlostpointercapture = releaseCapture;
  hold.oncontextmenu = event => event.preventDefault();
  hold.onkeydown = event => {
    if ((event.code === 'Space' || event.code === 'Enter') && !event.repeat) { event.preventDefault(); beginCapture(); }
  };
  hold.onkeyup = event => {
    if (event.code === 'Space' || event.code === 'Enter') { event.preventDefault(); releaseCapture(); }
  };
  window.addEventListener('blur', releaseCapture);
  document.addEventListener('visibilitychange', () => { if (document.hidden) releaseCapture(); });

  function renderUser(data) {
    serverBusy = data.busy;
    for (const room of Object.keys(rooms)) {
      const value = data.devices[room].confirmed;
      const card = element(room + '-state');
      card.classList.toggle('on', value === true);
      card.querySelector('span').textContent = value === null ? 'Unconfirmed' : value ? 'On' : 'Off';
    }
    if (lastSequence === null && data.events.length) {
      lastReply = data.events.at(-1);
      element('replay').hidden = false;
    }
    const newest = data.events.at(-1)?.sequence || 0;
    const reset = lastSequence !== null && newest < lastSequence;
    if (reset) lastSequence = 0;
    const changed = reset || lastSequence !== newest;
    const fresh = lastSequence === null ? [] : data.events.filter(event => event.sequence > lastSequence);
    for (const event of fresh) {
      speechQueue.push(event);
      if (event.kind !== 'acknowledgement') waiting = false;
    }
    lastSequence = newest;
    if (changed && data.events.length) {
      const log = element('feedback-log');
      log.replaceChildren();
      for (const event of data.events.slice(-20)) {
        const item = document.createElement('li');
        item.className = event.kind;
        item.textContent = event.message;
        log.appendChild(item);
      }
      log.scrollTop = log.scrollHeight;
    }
    if (!held && !closing && (waiting || serverBusy)) status('Processing your request…');
    updateControls();
    playNext();
  }

  async function renderAdmin() {
    const data = await json('/api/system');
    element('admin-health').textContent = `${data.health.node_red ? 'Node-RED ready' : 'Node-RED unavailable'} · ${data.health.ollama ? 'Ollama ready' : 'Ollama unavailable'} · ${data.speech_output.backend} · ${data.health.model}`;
    element('admin-transcript').textContent = data.last_transcript ? 'Heard: ' + data.last_transcript : 'No transcript yet';
    element('admin-progress').textContent = data.voice_status;
    for (const room of Object.keys(rooms)) {
      const state = data.devices[room];
      element(room + '-detail').textContent = `${state.confirmed === null ? 'Unconfirmed' : state.confirmed ? 'On' : 'Off'} · ${state.status}`;
      if (document.activeElement !== element(room + '-outcome')) element(room + '-outcome').value = data.mocks[room].outcome;
    }
    const events = data.events.map(event => `${event.kind}${event.reply_source ? ' [' + event.reply_source + ']' : ''} · ${event.message}`).join('\n');
    if (element('admin-events').textContent !== events) {
      element('admin-events').textContent = events;
      element('admin-events').scrollTop = element('admin-events').scrollHeight;
    }
    const media = await json('/api/status');
    element('admin-media').textContent = `${media.state} · audio ${media.audio_enabled ? 'on' : 'off'} · video ${media.video_enabled ? 'on' : 'off'} · ${media.fps} FPS`;
    if (media.video_enabled && media.frame_age_ms !== null && media.frame_age_ms < 3000) {
      const response = await request('/api/frame.jpg');
      if (response.status === 200) {
        const url = URL.createObjectURL(await response.blob());
        if (previewURL) URL.revokeObjectURL(previewURL);
        previewURL = url;
        element('admin-preview').src = url;
        element('admin-preview').hidden = false;
      }
    } else element('admin-preview').hidden = true;
  }

  async function poll() {
    clearTimeout(timer);
    try {
      renderUser(await json('/api/user'));
      if (admin) await renderAdmin();
    } catch (error) {
      if (admin && error.status === 401) {
        showUser();
        element('admin-dialog').showModal();
      } else if (!held && !closing) status('Gateway unavailable. Check that the system is running.');
    }
    timer = setTimeout(poll, 500);
  }
  function showUser() {
    admin = false;
    element('user-view').hidden = false;
    element('admin-view').hidden = true;
    history.replaceState(null, '', '/');
  }
  function showAdmin() {
    admin = true;
    element('user-view').hidden = true;
    element('admin-view').hidden = false;
    history.replaceState(null, '', '/?view=monitor');
  }
  element('admin-open').onclick = async () => {
    if (capture || closing) return;
    try {
      if ((await json('/api/admin')).admin) showAdmin();
      else { element('admin-dialog').showModal(); element('admin-password').focus(); }
    } catch (error) { status(error.message); }
  };
  element('admin-cancel').onclick = () => element('admin-dialog').close();
  element('admin-login').onsubmit = async event => {
    event.preventDefault();
    try {
      await json('/api/admin/login', {password: element('admin-password').value});
      element('admin-password').value = '';
      element('admin-error').textContent = '';
      element('admin-dialog').close();
      showAdmin();
    } catch (error) { element('admin-error').textContent = error.message; }
  };
  element('admin-back').onclick = showUser;
  element('admin-logout').onclick = async () => { await json('/api/admin/logout', {}); showUser(); };
  element('admin-command').onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector('button');
    button.disabled = true;
    try { await json('/api/input', {request_id: crypto.randomUUID(), source: 'text', text: element('test-text').value}); }
    catch (error) { element('admin-health').textContent = error.message; }
    finally { button.disabled = false; }
  };
  window.addEventListener('pagehide', () => {
    clearTimeout(timer);
    releaseCapture();
    capture?.stream?.getTracks().forEach(track => track.stop());
    player.pause();
  });
  if (new URLSearchParams(location.search).get('view') === 'monitor') element('admin-open').click();
  updateControls();
  poll();
})();

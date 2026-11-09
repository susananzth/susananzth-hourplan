// Dictation recorder: records with the microphone, converts to 16 kHz mono 16-bit WAV in the
// browser (so the server needs no ffmpeg) and uploads it. Nothing is sent until the user stops.
(function () {
  "use strict";

  var SAMPLE_RATE = 16000;
  var root = document.querySelector("[data-voice-recorder]");
  if (!root) { return; }

  var startButton = root.querySelector("[data-voice-start]");
  var stopButton = root.querySelector("[data-voice-stop]");
  var cancelButton = root.querySelector("[data-voice-cancel]");
  var message = root.querySelector("[data-voice-message]");
  var clock = root.querySelector("[data-voice-clock]");
  var maxSeconds = parseInt(root.dataset.maxSeconds, 10) || 180;

  var recorder = null;
  var stream = null;
  var chunks = [];
  var timer = null;
  var startedAt = 0;
  var cancelled = false;

  function csrf() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function say(text) { message.textContent = text; }
  function show(recording) {
    startButton.hidden = recording;
    stopButton.hidden = !recording;
    cancelButton.hidden = !recording;
  }
  function release() {
    if (timer) { clearInterval(timer); timer = null; }
    if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
    show(false);
  }

  function encodeWav(samples) {
    var buffer = new ArrayBuffer(44 + samples.length * 2);
    var view = new DataView(buffer);
    function text(offset, value) {
      for (var i = 0; i < value.length; i++) { view.setUint8(offset + i, value.charCodeAt(i)); }
    }
    text(0, "RIFF");
    view.setUint32(4, 36 + samples.length * 2, true);
    text(8, "WAVE");
    text(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, SAMPLE_RATE, true);
    view.setUint32(28, SAMPLE_RATE * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    text(36, "data");
    view.setUint32(40, samples.length * 2, true);
    for (var i = 0; i < samples.length; i++) {
      var s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buffer;
  }

  function toWav(blob) {
    var AudioCtx = window.AudioContext || window.webkitAudioContext;
    var context = new AudioCtx({ sampleRate: SAMPLE_RATE });
    return blob.arrayBuffer()
      .then(function (data) { return context.decodeAudioData(data); })
      .then(function (audio) {
        context.close();
        return encodeWav(audio.getChannelData(0));
      });
  }

  function upload(wav) {
    say(window.hpT("Enviando el dictado…"));
    return fetch(root.dataset.uploadUrl, {
      method: "POST",
      headers: { "X-CSRFToken": csrf(), "Content-Type": "audio/wav" },
      body: wav,
      credentials: "same-origin"
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) { throw new Error(body.error || window.hpT("No se pudo enviar el dictado.")); }
        window.location.href = body.url;
      });
    });
  }

  function finish() {
    var blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
    chunks = [];
    release();
    if (cancelled) { say(window.hpT("Dictado cancelado.")); return; }
    say(window.hpT("Preparando el audio…"));
    toWav(blob).then(upload).catch(function (err) {
      say(err && err.message ? err.message : window.hpT("No se pudo procesar el audio."));
    });
  }

  function start() {
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      say(window.hpT("Este navegador no permite grabar. Usa una versión reciente de Chrome, Edge o Firefox."));
      return;
    }
    if (!window.isSecureContext) {
      say(window.hpT("El micrófono solo funciona en HTTPS o en localhost."));
      return;
    }
    cancelled = false;
    navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
      .then(function (media) {
        stream = media;
        chunks = [];
        recorder = new MediaRecorder(media);
        recorder.ondataavailable = function (event) { if (event.data.size) { chunks.push(event.data); } };
        recorder.onstop = finish;
        recorder.start();
        startedAt = Date.now();
        show(true);
        say(window.hpT("Grabando… di fecha, horas, proyecto o actividad y qué hiciste."));
        timer = setInterval(function () {
          var seconds = Math.floor((Date.now() - startedAt) / 1000);
          clock.textContent = pad(Math.floor(seconds / 60)) + ":" + pad(seconds % 60);
          if (seconds >= maxSeconds) { stop(); }
        }, 250);
      })
      .catch(function () {
        say(window.hpT("No se pudo usar el micrófono. Revisa el permiso del navegador."));
      });
  }

  function stop() {
    if (recorder && recorder.state !== "inactive") { recorder.stop(); }
  }
  function cancel() {
    cancelled = true;
    stop();
  }

  startButton.addEventListener("click", start);
  stopButton.addEventListener("click", stop);
  cancelButton.addEventListener("click", cancel);
})();

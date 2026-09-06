/* Animated canvas favicon.
 *
 * Browsers don't animate a GIF set as the tab icon, so we decode
 * /static/favicon_anim_64.gif frame-by-frame with WebCodecs ImageDecoder and
 * repaint a <canvas> into the <link rel="icon"> href each frame. Where
 * ImageDecoder is unavailable we fall back to the static GIF (shows frame 1).
 * Animation pauses while the tab is hidden to avoid burning CPU in background.
 * Frame interval is floored at MIN_FRAME_MS (10 fps): ImageDecoder reports
 * duration in microseconds, and without a floor the GIF's ~20ms frames
 * encode a PNG into the tab icon ~50 times a second.
 */
(function () {
  "use strict";
  var SRC = "/static/favicon_anim_64.gif";
  var SIZE = 64;
  var MIN_FRAME_MS = 100;

  function iconLink() {
    var l = document.querySelector("link[rel~='icon']");
    if (!l) {
      l = document.createElement("link");
      l.rel = "icon";
      document.head.appendChild(l);
    }
    return l;
  }
  var link = iconLink();

  // No WebCodecs → static fallback and we're done.
  if (typeof window.ImageDecoder !== "function") {
    link.href = SRC;
    return;
  }

  var canvas = document.createElement("canvas");
  canvas.width = canvas.height = SIZE;
  var ctx = canvas.getContext("2d");
  var hidden = false;
  document.addEventListener("visibilitychange", function () {
    hidden = document.hidden;
  });

  fetch(SRC)
    .then(function (r) { return r.arrayBuffer(); })
    .then(function (buf) {
      var dec = new ImageDecoder({ data: buf, type: "image/gif" });
      return dec.tracks.ready.then(function () { return dec; });
    })
    .then(function (dec) {
      var track = dec.tracks.selectedTrack;
      var count = (track && track.frameCount) || 1;
      var i = 0;

      function tick() {
        if (hidden) { setTimeout(tick, 500); return; }
        dec.decode({ frameIndex: i % count }).then(function (res) {
          var frame = res.image;
          ctx.clearRect(0, 0, SIZE, SIZE);
          ctx.drawImage(frame, 0, 0, SIZE, SIZE);
          var durMs = frame.duration
            ? Math.max(MIN_FRAME_MS, frame.duration / 1000)
            : MIN_FRAME_MS;
          frame.close();
          link.href = canvas.toDataURL("image/png");
          i += 1;
          setTimeout(tick, durMs);
        }).catch(function () { link.href = SRC; });
      }
      tick();
    })
    .catch(function () { link.href = SRC; });
})();

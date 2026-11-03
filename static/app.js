/* Photo Bridge — ES5 only. iOS 9 Safari has no fetch(), no NodeList.forEach,
   no arrow functions. Uploads are downscaled first: a 4S photo is ~3 MB and a
   phone radio at ~200 KB/s makes that a 15-second upload plus a re-download. */
(function () {
  var picker = document.getElementById('picker');
  var status = document.getElementById('status');
  var fallback = document.getElementById('fallback');
  var gallery = document.querySelector('.gallery');
  var used = document.querySelector('.foot span');

  function say(text, isError) {
    if (!status) { return; }
    status.textContent = text;
    status.className = isError ? 'status err' : 'status';
  }

  if (!picker || !window.FormData || !window.XMLHttpRequest) {
    if (fallback) { fallback.hidden = false; }
    if (picker) { picker.style.display = 'block'; }
    return;
  }

  var MAX_EDGE = 1600;
  var QUALITY = 0.82;
  var SKIP_UNDER = 400 * 1024;

  function shrink(file, done) {
    if (!/^image\//.test(file.type) || file.size < SKIP_UNDER ||
        !window.URL || !window.URL.createObjectURL || !document.createElement('canvas').getContext) {
      return done(file);
    }
    var url = URL.createObjectURL(file);
    var img = new Image();
    var settled = false;
    function finish(payload) {
      if (settled) { return; }
      settled = true;
      try { URL.revokeObjectURL(url); } catch (e) {}
      done(payload);
    }
    img.onload = function () {
      try {
        var w = img.width, h = img.height;
        if (w > MAX_EDGE || h > MAX_EDGE) {
          var scale = Math.min(MAX_EDGE / w, MAX_EDGE / h);
          w = Math.round(w * scale);
          h = Math.round(h * scale);
        }
        var canvas = document.createElement('canvas');
        canvas.width = w;
        canvas.height = h;
        canvas.getContext('2d').drawImage(img, 0, 0, w, h);
        var dataURL = canvas.toDataURL('image/jpeg', QUALITY);
        var binary = atob(dataURL.split(',')[1]);
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i += 1) { bytes[i] = binary.charCodeAt(i); }
        finish(new Blob([bytes], { type: 'image/jpeg' }));
      } catch (err) {
        finish(file);                      // canvas refused: send the original
      }
    };
    img.onerror = function () { finish(file); };
    img.src = url;
  }

  function human(n) {
    var units = ['B', 'KB', 'MB', 'GB'], i = 0;
    while (n >= 1024 && i < 3) { n /= 1024; i += 1; }
    return (i === 0 ? n : n.toFixed(1)) + ' ' + units[i];
  }

  // Count and total come from the DOM, so they stay right after an upload or
  // a delete without a reload.
  function refresh() {
    var figures = gallery ? gallery.querySelectorAll('.tile') : [];
    var bytes = 0;
    for (var i = 0; i < figures.length; i += 1) {
      bytes += parseInt(figures[i].getAttribute('data-size'), 10) || 0;
    }
    var badge = document.querySelector('.count');
    if (badge) {
      badge.textContent = figures.length + (figures.length === 1 ? ' photo' : ' photos');
    }
    if (used) { used.textContent = human(bytes) + ' used'; }
  }

  // The server answers with the stored name. Draw the tile from the local blob:
  // the photo is visible at once and no bytes come back down the wire.
  function addTile(name, blob) {
    if (!gallery) { return; }
    var isVideo = /\.(mov|mp4)$/i.test(name);
    var src = (!isVideo && window.URL && window.URL.createObjectURL)
      ? window.URL.createObjectURL(blob)
      : '/media/' + encodeURIComponent(name);
    var figure = document.createElement('figure');
    figure.className = 'tile';
    figure.setAttribute('data-size', blob.size);
    figure.innerHTML =
      '<a class="thumb" href="/media/' + encodeURIComponent(name) + '">' +
      (isVideo
        ? '<video src="' + src + '" playsinline muted preload="metadata"></video>'
        : '<img src="' + src + '" alt="">') +
      '</a>' +
      '<button class="del" data-name="' + name + '" aria-label="Delete photo">&times;</button>' +
      '<figcaption>' + human(blob.size) + '</figcaption>';
    if (src.indexOf('blob:') === 0) { figure._blob = src; }
    gallery.insertBefore(figure, gallery.firstChild);
    wire(figure.querySelector('.del'));
    refresh();
  }

  var busy = false;

  picker.addEventListener('change', function () {
    var files = picker.files;
    if (!files || !files.length || busy) { return; }
    busy = true;
    var total = files.length;
    var index = 0;

    function step() {
      if (index >= total) {
        busy = false;
        picker.value = '';
        return;
      }
      var file = files[index];
      say('Preparing ' + (index + 1) + ' of ' + total + '\u2026');
      shrink(file, function (payload) {
        var fd = new FormData();
        try { fd.append('photo', payload, file.name); }
        catch (e) { fd.append('photo', payload); }
        var xhr = new XMLHttpRequest();
        xhr.open('POST', '/upload');
        xhr.upload.onprogress = function (event) {
          if (event.lengthComputable) {
            say('Uploading ' + (index + 1) + ' of ' + total +
                ' \u2014 ' + Math.round(event.loaded / event.total * 100) + '%');
          }
        };
        xhr.onload = function () {
          if (xhr.status === 200) {
            var saved = [];
            try { saved = JSON.parse(xhr.responseText).saved || []; } catch (e) {}
            if (saved.length) { addTile(saved[0], payload); }
            index += 1;
            say('Uploaded ' + index + ' of ' + total);
            step();
          } else {
            busy = false;
            var msg = 'Upload failed (' + xhr.status + ')';
            try { msg = JSON.parse(xhr.responseText).error || msg; } catch (e) {}
            say(msg, true);
          }
        };
        xhr.onerror = function () {
          busy = false;
          say('Network error during upload', true);
        };
        xhr.send(fd);
      });
    }

    step();
  });

  function wire(button) {
    if (!button) { return; }
    button.addEventListener('click', function (event) {
      event.preventDefault();
      var name = this.getAttribute('data-name');
      if (!window.confirm('Delete this photo?')) { return; }
      var button = this;
      var tileNode = this.parentNode;
      button.disabled = true;
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/delete');
      xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
      xhr.onload = function () {
        // 200 = deleted, 404 = already gone; both mean it is not there any more
        if (xhr.status === 200 || xhr.status === 404) {
          if (tileNode.parentNode) { tileNode.parentNode.removeChild(tileNode); }
          if (tileNode._blob) { try { URL.revokeObjectURL(tileNode._blob); } catch (e) {} }
          say('Deleted');
          refresh();
        } else {
          button.disabled = false;
          say('Delete failed (' + xhr.status + ')', true);
        }
      };
      xhr.onerror = function () {
        button.disabled = false;
        say('Network error during delete', true);
      };
      xhr.send('name=' + encodeURIComponent(name));
    });
  }

  var buttons = document.querySelectorAll('.del');
  for (var b = 0; b < buttons.length; b += 1) {
    wire(buttons[b]);
  }
  refresh();
})();

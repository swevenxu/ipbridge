/* 114s — ES5 only. iOS 9 Safari has no fetch(), no NodeList.forEach, no arrow
   functions. Files upload byte for byte: no downscale, no re-encode. */
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
      badge.textContent = figures.length + (figures.length === 1 ? ' item' : ' items');
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
      (isVideo ? '<span class="play"></span>' : '<img src="' + src + '" alt="">') +
      '</a>' +
      '<button class="del" data-name="' + name + '" aria-label="Delete file">&times;</button>' +
      '<figcaption>' + human(blob.size) + '</figcaption>';
    if (src.indexOf('blob:') === 0) { figure._blob = src; }
    gallery.insertBefore(figure, gallery.firstChild);
    wire(figure.querySelector('.del'));
    wireThumb(figure.querySelector('.thumb'));
    refresh();
  }

  var busy = false;

  function uploadAll(files) {
    if (!files || !files.length || busy) { return; }
    busy = true;
    var total = files.length;
    var index = 0;

    function step() {
      if (index >= total) {
        busy = false;
        if (picker) { picker.value = ''; }
        return;
      }
      var file = files[index];
      var fd = new FormData();
      try { fd.append('photo', file, file.name); }
      catch (e) { fd.append('photo', file); }
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
          if (saved.length) { addTile(saved[0], file); }
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
    }

    step();
  }

  picker.addEventListener('change', function () { uploadAll(picker.files); });

  // ---- drop files anywhere: pointer devices only, the phones never fire it ----
  var dragDepth = 0;

  function setClass(name, on) {
    var c = ' ' + document.body.className + ' ';
    if (on) {
      if (c.indexOf(' ' + name + ' ') < 0) { document.body.className += ' ' + name; }
    } else if (c.indexOf(' ' + name + ' ') >= 0) {
      document.body.className = c.replace(' ' + name + ' ', ' ')
        .replace(/^\s+|\s+$/g, '');
    }
  }

  function setDragging(on) { setClass('dragging', on); }

  document.addEventListener('dragenter', function (e) {
    if (!e.dataTransfer) { return; }
    dragDepth += 1;
    setDragging(true);
  }, false);
  document.addEventListener('dragover', function (e) {
    if (!e.dataTransfer) { return; }
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  }, false);
  document.addEventListener('dragleave', function () {
    dragDepth -= 1;
    if (dragDepth <= 0) { dragDepth = 0; setDragging(false); }
  }, false);
  document.addEventListener('drop', function (e) {
    dragDepth = 0;
    setDragging(false);
    if (!e.dataTransfer) { return; }
    e.preventDefault();
    uploadAll(e.dataTransfer.files);
  }, false);

  // ---- viewer: tap a tile to see it big, arrows / swipe / keys to move ----
  var box = null, stage = null, counter = null;
  var items = [], at = 0, isOpen = false;

  function buildViewer() {
    if (box) { return; }
    box = document.createElement('div');
    box.className = 'viewer';
    box.innerHTML =
      '<div class="vbar">' +
        '<span class="vcount"></span>' +
        '<button class="vclose" type="button" aria-label="Close">&times;</button>' +
      '</div>' +
      '<div class="vstage"></div>' +
      '<div class="vnav">' +
        '<button class="vprev" type="button" aria-label="Previous">&#8249;</button>' +
        '<button class="vnext" type="button" aria-label="Next">&#8250;</button>' +
      '</div>';
    document.body.appendChild(box);
    stage = box.querySelector('.vstage');
    counter = box.querySelector('.vcount');
    box.querySelector('.vclose').addEventListener('click', close, false);
    box.querySelector('.vprev').addEventListener('click', function () { show(at - 1); }, false);
    box.querySelector('.vnext').addEventListener('click', function () { show(at + 1); }, false);

    var x0 = null;
    box.addEventListener('touchstart', function (e) {
      if (e.touches.length === 1) { x0 = e.touches[0].clientX; }
    }, false);
    box.addEventListener('touchmove', function (e) {
      if (x0 !== null && e.touches.length === 1 &&
          Math.abs(e.touches[0].clientX - x0) > 12) {
        e.preventDefault();                        // a swipe is not a scroll
      }
    }, false);
    box.addEventListener('touchend', function (e) {
      if (x0 === null) { return; }
      var dx = e.changedTouches[0].clientX - x0;
      x0 = null;
      if (dx < -40) { show(at + 1); }
      else if (dx > 40) { show(at - 1); }
    }, false);
  }

  function tilesInOrder() {
    var out = [], figures = gallery ? gallery.querySelectorAll('.tile') : [];
    for (var i = 0; i < figures.length; i += 1) {
      var link = figures[i].querySelector('.thumb');
      if (link) { out.push({ figure: figures[i], href: link.getAttribute('href') }); }
    }
    return out;
  }

  function show(i) {
    if (!items.length) { return; }
    if (i < 0) { i = items.length - 1; }
    if (i >= items.length) { i = 0; }
    at = i;
    var item = items[i];
    var isVideo = /\.(mov|mp4)$/i.test(item.href);
    var src = item.href;
    var img = item.figure.querySelector('img');
    if (!isVideo && img && img.getAttribute('src')) { src = img.getAttribute('src'); }
    stage.innerHTML = isVideo
      ? '<video src="' + src + '" controls playsinline webkit-playsinline ' +
        'preload="metadata"></video>'
      : '<img src="' + src + '" alt="">';
    counter.textContent = (i + 1) + ' / ' + items.length;
  }

  function openAt(i) {
    buildViewer();
    items = tilesInOrder();
    if (!items.length) { return; }
    show(i);
    box.className = 'viewer on';
    setClass('locked', true);
    document.addEventListener('keydown', keys, false);
    isOpen = true;
  }

  function close() {
    if (!isOpen) { return; }
    isOpen = false;
    stage.innerHTML = '';                          // also stops a playing clip
    box.className = 'viewer';
    setClass('locked', false);
    document.removeEventListener('keydown', keys, false);
  }

  function keys(e) {
    var k = e.keyCode;
    if (k === 27) { close(); }
    else if (k === 37) { show(at - 1); }
    else if (k === 39) { show(at + 1); }
  }

  function openFrom(link) {
    var found = tilesInOrder();
    for (var i = 0; i < found.length; i += 1) {
      if (found[i].href === link.getAttribute('href')) { openAt(i); return; }
    }
  }

  function wireThumb(link) {
    if (!link) { return; }
    link.addEventListener('click', function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey || (e.button && e.button > 0)) {
        return;                                    // cmd-click still opens a tab
      }
      e.preventDefault();
      openFrom(this);
    }, false);
  }

  function wire(button) {
    if (!button) { return; }
    button.addEventListener('click', function (event) {
      event.preventDefault();
      var name = this.getAttribute('data-name');
      if (!window.confirm('Delete this file?')) { return; }
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
  var thumbs = document.querySelectorAll('.thumb');
  for (var t = 0; t < thumbs.length; t += 1) {
    wireThumb(thumbs[t]);
  }
  refresh();
})();

/* Photo Bridge — ES5 only. iOS 9 Safari has no fetch(), no NodeList.forEach,
   no arrow functions in production code paths we rely on. */
(function () {
  var picker = document.getElementById('picker');
  var status = document.getElementById('status');
  var form = document.getElementById('fallback');

  function say(text, isError) {
    if (!status) { return; }
    status.textContent = text;
    status.className = isError ? 'status err' : 'status';
  }

  if (!picker || !window.FormData || !window.XMLHttpRequest) {
    // old/odd browser: expose the plain form as a fallback
    if (form) { form.hidden = false; }
    if (picker) { picker.style.display = 'block'; }
    return;
  }

  var busy = false;

  picker.addEventListener('change', function () {
    var files = picker.files;
    if (!files || !files.length || busy) { return; }
    busy = true;
    var total = files.length;
    var i = 0;

    function next() {
      if (i >= total) {
        say('Uploaded ' + total + ' photo' + (total === 1 ? '' : 's'));
        setTimeout(function () { window.location.href = '/'; }, 700);
        return;
      }
      var fd = new FormData();
      fd.append('photo', files[i]);
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/upload');
      xhr.onload = function () {
        if (xhr.status === 200) {
          i += 1;
          say('Uploading ' + Math.min(i + 1, total) + ' of ' + total + '\u2026');
          next();
        } else if (xhr.status === 403) {
          say('Signed out. Reload the page.', true);
          busy = false;
        } else {
          var msg = 'Upload failed (' + xhr.status + ')';
          try { msg = JSON.parse(xhr.responseText).error || msg; } catch (e) {}
          say(msg, true);
          busy = false;
        }
      };
      xhr.onerror = function () {
        say('Network error during upload', true);
        busy = false;
      };
      say('Uploading ' + (i + 1) + ' of ' + total + '\u2026');
      xhr.send(fd);
    }

    next();
  });

  var buttons = document.querySelectorAll('.del');
  for (var b = 0; b < buttons.length; b += 1) {
    buttons[b].addEventListener('click', function (event) {
      event.preventDefault();
      var name = this.getAttribute('data-name');
      if (!window.confirm('Delete this photo?')) { return; }
      var tile = this.parentNode;
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/delete');
      xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
      xhr.onload = function () {
        if (xhr.status === 200) {
          if (tile.parentNode) { tile.parentNode.removeChild(tile); }
          say('Deleted');
        } else if (xhr.status === 403) {
          say('Signed out. Reload the page.', true);
        } else {
          say('Delete failed (' + xhr.status + ')', true);
        }
      };
      xhr.onerror = function () { say('Network error during delete', true); };
      xhr.send('name=' + encodeURIComponent(name));
    });
  }
})();

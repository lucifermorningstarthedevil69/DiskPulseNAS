/**
 * Web Media Player Hub & Visualizer
 *
 * Video playback is ffprobe/ffmpeg-aware:
 *   - Browser-native files (MP4 H.264/AAC) on their default audio stream play
 *     directly from /api/files/raw (fully seekable, zero CPU).
 *   - Anything else — MKV, exotic codecs, or a *non-default* audio track — is
 *     remuxed/transcoded on the fly via /api/media/stream with the chosen
 *     audio track (video is stream-copied when already H.264, so it's cheap).
 * Subtitles (embedded, extracted to WebVTT, or external sidecars) attach as
 * <track> elements. Playback speed applies to both audio and video.
 *
 * Video UI is a floating overlay (top bar + bottom control bar) that fades out
 * during playback: tap = play/pause, double-tap edges = seek ±10s, scrub bar
 * shows ffmpeg-grabbed thumbnail previews, and a single gear menu consolidates
 * speed / audio-track / subtitle selection. Audio keeps the card controls.
 */
class WebMediaPlayer {
  constructor() {
    this.videoEl = document.getElementById('media-video-element');
    this.audioEl = document.getElementById('media-audio-element');
    this.videoContainer = document.getElementById('media-video-container');
    this.audioContainer = document.getElementById('media-audio-container');
    this.playerCard = this.videoEl ? this.videoEl.closest('.media-player-card') : null;
    this.canvas = document.getElementById('audio-visualizer-canvas');

    this.activePlayer = 'audio'; // 'audio' | 'video'
    this.playlist = [];
    this.currentIndex = -1;
    this.isPlaying = false;

    // Advanced video state
    this.videoRelPath = null;
    this.videoTitle = '';
    this.mediaInfo = null;
    this.isTranscoded = false;
    this.defaultAudioIdx = 0;
    this.currentAudioIdx = 0;
    this.knownDuration = 0;
    this.baseTime = 0;             // content-time where the current source begins
    this.currentSubtitle = null;   // {kind, track?, file?, label, value}
    this.playbackRate = 1;
    this._pendingPlay = true;
    this._scrubbing = false;       // user is dragging the seek bar

    // Settings-menu data (populated from ffprobe info)
    this.SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
    this.audioTracks = [];
    this.subtitleOptions = [];
    this.ffmpegHintMsg = '';
    this._menuView = 'main';
    this._lastSubValue = null;     // CC toggle restores the last picked track

    // Overlay state
    this.HIDE_DELAY_MS = 2600;
    this._hideTimer = null;
    this._clickTimer = null;       // single vs double tap disambiguation
    this._scrubFrac = 0;
    this._thumbCache = new Map();  // second -> object URL (per video)
    this._thumbPending = null;
    this._thumbLastReq = 0;
    this._thumbsDead = false;      // server can't produce frames (no ffmpeg)
    this._bookmarks = this.loadBookmarks();

    // Cast (Remote Playback) state
    this._castOrigin = null;       // LAN origin media URLs use while casting
    this._mutedBeforeCast = false;

    if (this.videoEl) this.videoEl.volume = 0.8;
    if (this.audioEl) this.audioEl.volume = 0.8;

    this.initVisualizer();
    this.bindEvents();
    this.bindVideoOverlay();
    this.loadMediaLibrary();
  }

  // ------------------------------------------------------------------ events

  bindEvents() {
    // Audio card controls
    document.getElementById('media-btn-play')?.addEventListener('click', () => this.togglePlay());
    document.getElementById('media-btn-prev')?.addEventListener('click', () => this.playPrev());
    document.getElementById('media-btn-next')?.addEventListener('click', () => this.playNext());

    // Audio scrub bar — preview while dragging, commit on release.
    const seekBar = document.getElementById('media-seek-bar');
    seekBar?.addEventListener('input', (e) => {
      const dur = this.effectiveDuration();
      if (dur <= 0) return;
      this._scrubbing = true;
      const t = (e.target.value / 100) * dur;
      const curEl = document.getElementById('media-current-time');
      if (curEl) curEl.textContent = this.formatTime(t);
    });
    seekBar?.addEventListener('change', (e) => {
      this._scrubbing = false;
      const dur = this.effectiveDuration();
      if (dur > 0) this.seekTo((e.target.value / 100) * dur);
    });

    // Audio volume bar (shared with the video element)
    document.getElementById('media-volume-bar')?.addEventListener('input', (e) => {
      this.applyVolume(parseFloat(e.target.value));
    });

    // Audio speed selector — remembered so it survives a source reload
    document.getElementById('media-speed-select')?.addEventListener('change', (e) => {
      this.setSpeed(parseFloat(e.target.value) || 1);
    });

    // Media element events
    [this.videoEl, this.audioEl].forEach(el => {
      if (!el) return;
      el.addEventListener('timeupdate', () => this.updateTime());
      el.addEventListener('ended', () => this.playNext());
    });

    if (this.videoEl) {
      this.videoEl.addEventListener('play', () => {
        this.isPlaying = true;
        this.updatePlayButton();
        this.pokeControls();
      });
      this.videoEl.addEventListener('pause', () => {
        this.isPlaying = false;
        this.updatePlayButton();
        this.pokeControls();
      });
    }

    // Refresh Library
    document.getElementById('media-refresh-btn')?.addEventListener('click', () => this.loadMediaLibrary());

    // Releasing the stream on tab close / hide (bfcache) so a lingering ffmpeg
    // transcode doesn't keep the source file locked after the page goes away.
    window.addEventListener('pagehide', () => this.stopPlayback());
  }

  /** All floating-overlay bindings for video mode. */
  bindVideoOverlay() {
    const wrap = this.videoContainer;
    if (!wrap) return;
    const on = (id, ev, fn) => document.getElementById(id)?.addEventListener(ev, fn);

    // Top bar
    on('vp-btn-back', 'click', () => this.goBack());
    on('vp-btn-bookmark', 'click', () => this.toggleBookmark());
    on('vp-btn-cast', 'click', () => this.promptCast());

    // Remote Playback (cast) lifecycle: while a device is connected the
    // element's timeline drives the remote, so pausing the element would
    // pause the cast too — silence the local speakers instead, and hand
    // playback back to this device when the connection drops.
    [this.videoEl, this.audioEl].forEach(el => {
      const remote = el && el.remote;
      if (!remote || typeof remote.addEventListener !== 'function') return;
      remote.addEventListener('connect', () => {
        this._mutedBeforeCast = el.muted;
        el.muted = true;
        this.toast('Casting to remote device');
        this.pokeControls();
      });
      remote.addEventListener('disconnect', () => {
        el.muted = this._mutedBeforeCast;
        if (el === this.videoEl) this.endCastMode();
        else this._castOrigin = null;
      });
    });

    // Transport
    on('vp-btn-play', 'click', () => this.togglePlay());
    on('vp-btn-prev', 'click', () => this.playPrev());
    on('vp-btn-next', 'click', () => this.playNext());
    on('vp-btn-mute', 'click', () => this.toggleMute());
    on('vp-volume', 'input', (e) => this.applyVolume(parseFloat(e.target.value)));

    // Quick settings (right group)
    on('vp-btn-cc', 'click', () => this.toggleCc());
    on('vp-btn-speed', 'click', () => this.toggleSettings('speed'));
    on('vp-btn-settings', 'click', () => this.toggleSettings('main'));
    on('vp-btn-fullscreen', 'click', () => this.toggleFullscreen());

    // Settings menu (event delegation — contents are re-rendered per view)
    document.getElementById('vp-menu')?.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-action]');
      if (!btn || btn.disabled) return;
      const { action, value } = btn.dataset;
      if (action === 'back') this.openSettings('main');
      else if (action === 'open') this.openSettings(value);
      else if (action === 'speed') this.setSpeed(parseFloat(value));
      else if (action === 'audio') this.changeAudioTrack(parseInt(value, 10) || 0);
      else if (action === 'sub') {
        this.changeSubtitle(value, btn.querySelector('span')?.textContent || '');
      }
    });

    // Auto-hide: any pointer motion over the player re-shows the controls.
    wrap.addEventListener('pointermove', () => this.pokeControls());
    wrap.addEventListener('pointerleave', () => {
      if (this.isPlaying && !this._scrubbing && !this.isMenuOpen()) this.hideControls();
    });

    this.bindGestures();
    this.bindSeekBar();
    this.bindKeyboard();

    document.addEventListener('fullscreenchange', () => this.onFullscreenChange());
    document.addEventListener('webkitfullscreenchange', () => this.onFullscreenChange());
  }

  onFullscreenChange() {
    this.updateFullscreenIcon();
    this.syncOrientationLock();
  }

  /** Single tap = play/pause; double tap left/right third = seek ±10s,
   *  double tap center = play/pause (instant, skips the tap delay). */
  bindGestures() {
    const gesture = document.getElementById('vp-gesture');
    if (!gesture) return;

    gesture.addEventListener('click', () => {
      this.pokeControls();
      if (this._clickTimer) return; // second click of a double tap — dblclick handles it
      this._clickTimer = setTimeout(() => {
        this._clickTimer = null;
        this.togglePlay();
      }, 240);
    });

    gesture.addEventListener('dblclick', (e) => {
      if (this._clickTimer) {
        clearTimeout(this._clickTimer);
        this._clickTimer = null;
      }
      const rect = gesture.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width;
      if (x < 0.33) this.seekBy(-10);
      else if (x > 0.67) this.seekBy(10);
      else this.togglePlay();
    });
  }

  /** Custom scrub bar: drag to scrub (commits on release, like the old range
   *  input — a transcoded stream is re-requested once, not per pixel), plus a
   *  hover thumbnail preview fed by /api/media/thumb. */
  bindSeekBar() {
    const bar = document.getElementById('vp-seek');
    if (!bar) return;

    const fracFromEvent = (e) => {
      const r = bar.getBoundingClientRect();
      return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    };

    bar.addEventListener('pointerdown', (e) => {
      if (this.effectiveDuration() <= 0) return;
      try { bar.setPointerCapture(e.pointerId); } catch (_) {}
      this._scrubbing = true;
      this._scrubFrac = fracFromEvent(e);
      this.videoContainer.classList.add('vp-scrubbing');
      this.paintScrub(this._scrubFrac);
      this.labelScrubTime(this._scrubFrac);
      this.pokeControls();
    });

    bar.addEventListener('pointermove', (e) => {
      const f = fracFromEvent(e);
      if (this._scrubbing) {
        this._scrubFrac = f;
        this.paintScrub(f);
        this.labelScrubTime(f);
      }
      this.showPreview(f);
      this.pokeControls();
    });

    const endScrub = () => {
      if (!this._scrubbing) return;
      this._scrubbing = false;
      this.videoContainer.classList.remove('vp-scrubbing');
      const dur = this.effectiveDuration();
      if (dur > 0) this.seekTo(this._scrubFrac * dur);
      this.pokeControls();
    };
    bar.addEventListener('pointerup', endScrub);
    bar.addEventListener('pointercancel', endScrub);

    bar.addEventListener('pointerleave', () => {
      if (!this._scrubbing) this.hidePreview();
    });
  }

  /** Player-wide keyboard shortcuts (only while a video is on screen). */
  bindKeyboard() {
    window.addEventListener('keydown', (e) => {
      if (this.activePlayer !== 'video') return;
      if (!this.videoContainer || this.videoContainer.style.display === 'none') return;
      const t = e.target;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
                t.tagName === 'SELECT' || t.isContentEditable)) return;

      switch (e.key.toLowerCase()) {
        case ' ':
        case 'k':
          e.preventDefault();
          this.togglePlay();
          break;
        case 'arrowleft':
          e.preventDefault();
          this.seekBy(-5);
          break;
        case 'arrowright':
          e.preventDefault();
          this.seekBy(5);
          break;
        case 'j':
          this.seekBy(-10);
          break;
        case 'l':
          this.seekBy(10);
          break;
        case 'arrowup':
          e.preventDefault();
          this.nudgeVolume(0.05);
          break;
        case 'arrowdown':
          e.preventDefault();
          this.nudgeVolume(-0.05);
          break;
        case 'f':
          e.preventDefault();
          this.toggleFullscreen();
          break;
        case 'm':
          this.toggleMute();
          break;
        case 'c':
          this.toggleCc();
          break;
        case 'escape':
          if (this.isMenuOpen()) this.closeSettings();
          break;
        default:
          return;
      }
      this.pokeControls();
    });
  }

  // ------------------------------------------------------------- core helpers

  getActiveElement() {
    return this.activePlayer === 'video' ? this.videoEl : this.audioEl;
  }

  /** Duration that drives the scrub bar.
   *
   *  For VIDEO this is always the full-film duration from ffprobe
   *  (`knownDuration`). A transcoded stream started mid-film (`baseTime > 0`)
   *  only *contains* the remaining segment, so the <video> element's own
   *  `duration` is `total − baseTime` — using it would shrink the bar to the
   *  remaining part and cap forward-seeking to that window (the "0:08 / 0:05"
   *  bug after switching audio or seeking). Fall back to `baseTime + el.duration`
   *  only when ffprobe gave us nothing. */
  effectiveDuration() {
    const el = this.getActiveElement();
    const elDur = (el && isFinite(el.duration) && el.duration > 0) ? el.duration : 0;
    if (this.activePlayer === 'video') {
      if (this.knownDuration > 0) return this.knownDuration;
      if (elDur > 0) return (this.baseTime || 0) + elDur; // segment length + offset ≈ total
      return 0;
    }
    return elDur;
  }

  applyPlaybackRate() {
    if (this.videoEl) this.videoEl.playbackRate = this.playbackRate;
    if (this.audioEl) this.audioEl.playbackRate = this.playbackRate;
  }

  /** Fullscreen the whole player card (video + overlay controls) so the scrub
   *  bar and settings menu stay usable — the native <video> controls are
   *  hidden during managed playback. */
  toggleFullscreen() {
    if (this.activePlayer !== 'video') return;
    const target = this.playerCard || this.videoContainer;
    if (!target) return;
    const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
    if (fsEl) {
      (document.exitFullscreen || document.webkitExitFullscreen || function () {}).call(document);
    } else {
      (target.requestFullscreen || target.webkitRequestFullscreen || function () {}).call(target);
    }
  }

  updateFullscreenIcon() {
    const btn = document.getElementById('vp-btn-fullscreen');
    if (!btn) return;
    const fs = document.fullscreenElement || document.webkitFullscreenElement;
    btn.innerHTML = `<i data-lucide="${fs ? 'minimize' : 'maximize'}"></i>`;
    if (window.lucide) lucide.createIcons();
  }

  /** On phones, rotate to landscape while the video is fullscreen for a proper
   *  cinema view, and release the lock when leaving fullscreen. Feature-detected
   *  and promise-guarded so it silently no-ops on desktop, iOS Safari, or any
   *  browser without the Screen Orientation API (the lock only works while an
   *  element is actually fullscreen, which is exactly when we call it). */
  syncOrientationLock() {
    const orientation = screen.orientation;
    if (!orientation) return;
    const fs = document.fullscreenElement || document.webkitFullscreenElement;
    if (fs) {
      if (typeof orientation.lock === 'function') {
        Promise.resolve(orientation.lock('landscape')).catch(() => {});
      }
    } else if (typeof orientation.unlock === 'function') {
      try { orientation.unlock(); } catch (_) {}
    }
  }

  // ----------------------------------------------------------------- library

  async loadMediaLibrary() {
    try {
      // Find all audio & video files
      const audioFiles = await api.searchFiles('.wav');
      const mp3Files = await api.searchFiles('.mp3');
      const mkvFiles = await api.searchFiles('.mkv');
      const mp4Files = await api.searchFiles('.mp4');

      const allMedia = [...audioFiles, ...mp3Files, ...mkvFiles, ...mp4Files];
      this.playlist = allMedia;
      this.renderPlaylist();
    } catch (err) {
      console.error(err);
    }
  }

  renderPlaylist() {
    const container = document.getElementById('media-playlist-container');
    if (!container) return;

    if (!this.playlist.length) {
      container.innerHTML = '<p style="text-align: center; color: var(--text-dim); padding: 24px;">No media files found in storage.</p>';
      return;
    }

    container.innerHTML = this.playlist.map((item, idx) => {
      const isCurrent = this.currentIndex === idx;
      const isVid = item.category === 'video';
      return `
        <div class="nav-item ${isCurrent ? 'active' : ''}" style="display: flex; justify-content: space-between; padding: 10px 14px;" onclick="mediaPlayer.playByIndex(${idx})">
          <div style="display: flex; align-items: center; gap: 10px; overflow: hidden;">
            <i data-lucide="${isVid ? 'film' : 'music'}" style="color: ${isVid ? 'var(--accent-rose)' : 'var(--accent-violet)'}; flex-shrink: 0;"></i>
            <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${item.name}</span>
          </div>
          <span style="font-size: 0.75rem; color: var(--text-dim);">${item.size_human}</span>
        </div>
      `;
    }).join('');

    if (window.lucide) lucide.createIcons();
  }

  playByIndex(index) {
    if (index < 0 || index >= this.playlist.length) return;
    this.currentIndex = index;
    const item = this.playlist[index];

    if (item.category === 'video') {
      this.playVideo(api.getRawFileUrl(item.path), item.name, item.path);
    } else {
      this.playAudio(api.getRawFileUrl(item.path), item.name);
    }
    this.renderPlaylist();
  }

  // ----------------------------------------------------------- video playback

  /**
   * Start video playback. `relPath` (storage-relative) unlocks the advanced
   * ffmpeg features; without it we fall back to a plain direct stream.
   */
  async playVideo(url, title, relPath = null) {
    this.activePlayer = 'video';
    this.audioEl.pause();
    this.audioContainer.style.display = 'none';
    this.videoContainer.style.display = 'flex';
    if (this.playerCard) this.playerCard.classList.add('vp-video-mode');

    this.videoRelPath = relPath;
    this.videoTitle = title;
    this.mediaInfo = null;
    this.knownDuration = 0;
    this.baseTime = 0;
    this.currentSubtitle = null;
    this.audioTracks = [];
    this.subtitleOptions = [];
    this.ffmpegHintMsg = '';
    this._pendingPlay = true;
    this._lastSubValue = null;
    this.resetThumbCache();

    const titleEl = document.getElementById('vp-title');
    if (titleEl) titleEl.textContent = title || '';
    this.videoContainer.classList.remove('vp-native');
    this.closeSettings();
    this.updateCcState();
    this.updateBookmarkState();
    this.paintScrub(0);
    this.setOverlayTimes(0, 0);

    // No rel path (legacy caller) → simplest possible playback.
    if (!relPath) {
      this.startNativeFallback(url);
      return;
    }

    let info = null;
    try {
      info = await api.getMediaInfo(relPath);
    } catch (e) {
      info = null;
    }
    // Guard against a race where the user picked another track meanwhile.
    if (this.videoRelPath !== relPath) return;

    this.mediaInfo = info;
    this.ffmpegHintMsg = this.computeFfmpegHint(info);
    if (info && info.ok) {
      this.knownDuration = info.duration || 0;
      const audio = info.audio || [];
      this.defaultAudioIdx = Math.max(0, audio.findIndex(a => a.default));
      if (this.defaultAudioIdx < 0) this.defaultAudioIdx = 0;
      this.currentAudioIdx = this.defaultAudioIdx;

      this.storeTrackData(info);
      this.setOverlayTimes(0, this.knownDuration);

      // Resume from a saved bookmark (skip it when too close to either end).
      let startTime = 0;
      const bm = this._bookmarks[relPath];
      if (bm && this.knownDuration > 0 && bm > 30 && bm < this.knownDuration - 20) {
        startTime = bm;
        this.toast(`Resuming from bookmark at ${this.formatTime(bm)}`);
      }

      // Direct-play only when the container/codecs are browser-native AND we're
      // on the default audio track; otherwise remux/transcode via ffmpeg.
      const useStream = !info.direct_play;
      this.loadVideoSource({ useStream, audioIdx: this.currentAudioIdx, startTime, autoplay: true });
    } else {
      // ffprobe unavailable or failed → best-effort direct playback.
      this.startNativeFallback(url);
    }
  }

  /** Best-effort playback with the browser's native controls: no ffprobe
   *  duration means the custom scrub bar can't be trusted, so hand over to the
   *  <video> element and keep only the overlay top bar (back / title). */
  startNativeFallback(url) {
    this.isTranscoded = false;
    this.videoEl.controls = true;
    this.videoContainer.classList.add('vp-native');
    this.videoEl.src = url;
    this.applyPlaybackRate();
    this.videoEl.play().catch(() => {});
    this.isPlaying = true;
    this.updatePlayButton();
  }

  /**
   * (Re)point the <video> at either the raw file or the ffmpeg stream.
   *
   * `startTime` is the *content-time* where playback should begin:
   *   - streamed: ffmpeg `-ss` positions the segment, so the element timeline
   *     is 0-based and `baseTime` records the offset (subtitles are shifted to
   *     match); this keeps arbitrary seeks on transcoded media snappy.
   *   - raw/direct: the file always starts at 0, so we restore the position by
   *     setting `currentTime` once metadata is ready.
   */
  loadVideoSource({ useStream, audioIdx = 0, startTime = 0, autoplay = true } = {}) {
    const vcodec = (this.mediaInfo && this.mediaInfo.video && this.mediaInfo.video.codec) || '';
    this.isTranscoded = !!useStream;
    const start = startTime > 0 ? startTime : 0;

    // The custom control bar is the single, authoritative scrubber here: it
    // knows the true film length (knownDuration) and does absolute seeking by
    // re-requesting the transcode. The browser's native <video controls> bar
    // only sees the current (possibly mid-film) segment, so showing it too
    // gives a second, disagreeing timeline that can't fast-forward. Hide it.
    if (this.videoEl) this.videoEl.controls = false;
    this.videoContainer.classList.remove('vp-native');

    let src, restoreTo;
    if (useStream) {
      this.baseTime = start;
      restoreTo = 0;  // -ss already positioned the segment
      src = api.getMediaStreamUrl(this.videoRelPath, audioIdx, start, vcodec);
    } else {
      this.baseTime = 0;
      restoreTo = start;
      src = api.getRawFileUrl(this.videoRelPath);
    }
    // While casting, the remote device fetches the stream — keep it on the
    // LAN origin resolved by promptCast() instead of the page's origin.
    if (this._castOrigin) src = src.replace(window.location.origin, this._castOrigin);

    const shouldPlay = autoplay && this._pendingPlay !== false;

    const onMeta = () => {
      this.videoEl.removeEventListener('loadedmetadata', onMeta);
      this.applyPlaybackRate();
      this.applySubtitle();
      if (restoreTo > 0) {
        try { this.videoEl.currentTime = restoreTo; } catch (_) {}
      }
      if (shouldPlay) this.videoEl.play().catch(() => {});
      this.updatePlayButton();
      this.updateTime();
    };
    this.videoEl.addEventListener('loadedmetadata', onMeta);

    this._pendingPlay = true;
    this.videoEl.src = src;
    this.videoEl.load();
    this.isPlaying = shouldPlay;
    this.updatePlayButton();
  }

  /**
   * Seek to an absolute content-time. Direct-play files seek natively; a
   * transcoded stream is re-requested from that time (ffmpeg `-ss` + re-encode
   * lands within a frame of the request), so forward seeks don't have to buffer
   * from the start of the film and the clock/subtitles stay aligned.
   */
  seekTo(absTime) {
    const el = this.getActiveElement();
    if (!el) return;
    if (this.activePlayer === 'video' && this.isTranscoded) {
      this._pendingPlay = !el.paused;
      this.loadVideoSource({
        useStream: true,
        audioIdx: this.currentAudioIdx,
        startTime: absTime,
        autoplay: true,
      });
    } else {
      try { el.currentTime = absTime; } catch (_) {}
    }
  }

  /** Relative seek (gestures / arrow keys) with edge clamping. */
  seekBy(delta) {
    if (this.activePlayer !== 'video' || !this.videoEl) return;
    const dur = this.effectiveDuration();
    if (dur <= 0) return;
    const cur = (this.baseTime || 0) + (this.videoEl.currentTime || 0);
    const t = Math.min(Math.max(0, cur + delta), Math.max(0, dur - 0.5));
    this.showSeekIndicator(delta < 0 ? 'left' : 'right');
    this.seekTo(t);
  }

  // --------------------------------------------------- tracks & settings menu

  /** Turn ffprobe info into the data the settings menu renders from. */
  storeTrackData(info) {
    this.audioTracks = info.audio || [];

    const opts = [{ value: 'off', label: 'Off', disabled: false }];
    (info.subtitles || []).forEach(s => {
      if (s.text_based) {
        opts.push({ value: `emb:${s.rel_index}`, label: s.label, disabled: false });
      } else {
        opts.push({ value: '', label: `${s.label} (image-based — not selectable)`, disabled: true });
      }
    });
    (info.external_subs || []).forEach(e => {
      opts.push({ value: `ext:${encodeURIComponent(e.file)}`, label: `External: ${e.label}`, disabled: false });
    });
    this.subtitleOptions = opts;

    if (this.isMenuOpen()) this.renderSettingsMenu();
  }

  /**
   * Explain a missing ffmpeg/ffprobe so the absent audio & subtitle options
   * are surfaced in the settings menu rather than silently missing.
   */
  computeFfmpegHint(info) {
    if (info && (info.ffprobe === false || info.ffmpeg === false)) {
      return 'Install ffmpeg on the server to enable audio-track switching and subtitles.';
    }
    if (info && info.ok === false && info.ffprobe !== false) {
      return "Couldn't read this file's audio/subtitle tracks.";
    }
    return '';
  }

  isMenuOpen() {
    return document.getElementById('vp-menu')?.classList.contains('vp-menu-on') || false;
  }

  openSettings(view = 'main') {
    const menu = document.getElementById('vp-menu');
    if (!menu) return;
    this._menuView = view;
    this.renderSettingsMenu();
    menu.classList.add('vp-menu-on');
    this.pokeControls();
  }

  closeSettings() {
    document.getElementById('vp-menu')?.classList.remove('vp-menu-on');
    this.pokeControls();
  }

  toggleSettings(view = 'main') {
    if (this.isMenuOpen()) this.closeSettings();
    else this.openSettings(view);
  }

  renderSettingsMenu() {
    const menu = document.getElementById('vp-menu');
    if (!menu) return;
    const view = this._menuView;

    const head = (title, withBack) => `
      <div class="vp-menu-head">
        ${withBack ? '<button class="vp-menu-head-btn" data-action="back" title="Back"><i data-lucide="chevron-left"></i></button>' : ''}
        <span>${title}</span>
      </div>`;

    const row = (label, selected, action, value, disabled = false) => `
      <button class="vp-menu-item ${selected ? 'vp-selected' : ''}" data-action="${action}"
        data-value="${this.escHtml(value)}" ${disabled ? 'disabled' : ''}>
        <i data-lucide="check" class="vp-menu-check"></i>
        <span>${this.escHtml(label)}</span>
      </button>`;

    const mainRow = (icon, label, value, target) => `
      <button class="vp-menu-item" data-action="open" data-value="${target}">
        <i data-lucide="${icon}" class="vp-menu-check" style="visibility: visible; color: var(--text-muted);"></i>
        <span>${label}</span>
        <span class="vp-menu-value">${this.escHtml(value)}</span>
        <i data-lucide="chevron-right" class="vp-menu-caret"></i>
      </button>`;

    let html = '';
    if (view === 'main') {
      const audioLabel = (this.audioTracks.find(a => a.rel_index === this.currentAudioIdx) || {}).label || 'Default';
      const subLabel = this.currentSubtitle ? (this.currentSubtitle.label || 'On') : 'Off';
      html += head('Settings', false);
      html += '<div class="vp-menu-body">';
      html += mainRow('gauge', 'Playback speed', this.speedLabel(this.playbackRate), 'speed');
      if (this.audioTracks.length > 1) {
        html += mainRow('volume-2', 'Audio track', audioLabel, 'audio');
      }
      html += mainRow('captions', 'Subtitles', subLabel, 'subs');
      html += '</div>';
      if (this.ffmpegHintMsg) {
        html += `<div class="vp-menu-note">${this.escHtml(this.ffmpegHintMsg)}</div>`;
      }
    } else if (view === 'speed') {
      html += head('Playback speed', true);
      html += '<div class="vp-menu-body">';
      for (const s of this.SPEEDS) {
        html += row(this.speedLabel(s), Math.abs(this.playbackRate - s) < 0.001, 'speed', String(s));
      }
      html += '</div>';
    } else if (view === 'audio') {
      html += head('Audio track', true);
      html += '<div class="vp-menu-body">';
      for (const a of this.audioTracks) {
        html += row(a.label, a.rel_index === this.currentAudioIdx, 'audio', String(a.rel_index));
      }
      html += '</div>';
    } else if (view === 'subs') {
      html += head('Subtitles', true);
      html += '<div class="vp-menu-body">';
      for (const o of this.subtitleOptions) {
        const selected = o.value === 'off' ? !this.currentSubtitle
          : !!this.currentSubtitle && this.currentSubtitle.value === o.value;
        html += row(o.label, selected, 'sub', o.value, o.disabled);
      }
      html += '</div>';
    }

    menu.innerHTML = html;
    if (window.lucide) lucide.createIcons();
  }

  escHtml(s) {
    return String(s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  speedLabel(rate) {
    const r = parseFloat(rate) || 1;
    return `${Number.isInteger(r) ? r.toFixed(1) : String(r)}x`;
  }

  setSpeed(rate) {
    this.playbackRate = rate;
    this.applyPlaybackRate();
    const label = document.getElementById('vp-speed-label');
    if (label) label.textContent = this.speedLabel(rate);
    const sel = document.getElementById('media-speed-select');
    if (sel) sel.value = String(rate);
    if (this.isMenuOpen()) this.renderSettingsMenu();
  }

  changeAudioTrack(idx) {
    if (!this.videoRelPath || this.activePlayer !== 'video') return;
    this.currentAudioIdx = idx;

    // Default track on a browser-native file can play raw (frame-accurate seek);
    // any other track requires the ffmpeg stream. Resume where we were — the
    // streamed segment is re-encoded from absPos, so audio/video/subtitles all
    // realign to that instant.
    const canDirect = this.mediaInfo && this.mediaInfo.direct_play && idx === this.defaultAudioIdx;
    const absPos = (this.baseTime || 0) + (this.videoEl.currentTime || 0);
    this._pendingPlay = !this.videoEl.paused;
    this.loadVideoSource({ useStream: !canDirect, audioIdx: idx, startTime: absPos, autoplay: true });
    if (this.isMenuOpen()) this.renderSettingsMenu();
  }

  /** CC button: toggle between Off and the last-picked (or first) track. */
  toggleCc() {
    const picks = this.subtitleOptions.filter(o => !o.disabled && o.value !== 'off');
    if (!picks.length) {
      this.openSettings('subs'); // shows "Off" only, plus any ffmpeg hint
      return;
    }
    if (this.currentSubtitle) {
      this.changeSubtitle('off');
    } else {
      const pick = picks.find(o => o.value === this._lastSubValue) || picks[0];
      this.changeSubtitle(pick.value, pick.label);
    }
  }

  updateCcState() {
    const btn = document.getElementById('vp-btn-cc');
    if (btn) btn.classList.toggle('vp-active', !!this.currentSubtitle);
  }

  changeSubtitle(value, label = '') {
    if (!value || value === 'off') {
      this.currentSubtitle = null;
    } else if (value.startsWith('emb:')) {
      this.currentSubtitle = { kind: 'embedded', track: parseInt(value.slice(4), 10) || 0, label, value };
    } else if (value.startsWith('ext:')) {
      this.currentSubtitle = { kind: 'external', file: decodeURIComponent(value.slice(4)), label, value };
    }
    if (this.currentSubtitle) this._lastSubValue = value;
    this.applySubtitle();
    this.updateCcState();
    if (this.isMenuOpen()) this.renderSettingsMenu();
  }

  /** Clear existing <track>s and attach the currently selected subtitle. */
  applySubtitle() {
    if (!this.videoEl) return;
    Array.from(this.videoEl.querySelectorAll('track')).forEach(t => t.remove());
    // Hide any lingering text tracks.
    for (const tt of this.videoEl.textTracks) tt.mode = 'disabled';

    const sel = this.currentSubtitle;
    if (!sel || !this.videoRelPath) return;

    // Match the cue timeline to the current segment: a seeked/transcoded stream
    // starts at baseTime, so cues are shifted back by the same amount.
    const offset = this.isTranscoded ? (this.baseTime || 0) : 0;
    let src = api.getSubtitleUrl(this.videoRelPath, { ...sel, offset });
    if (this._castOrigin) src = src.replace(window.location.origin, this._castOrigin);
    const track = document.createElement('track');
    track.kind = 'subtitles';
    track.label = sel.label || 'Subtitles';
    track.srclang = 'und';
    track.default = true;
    track.src = src;
    this.videoEl.appendChild(track);

    const showIt = () => {
      for (const tt of this.videoEl.textTracks) tt.mode = 'showing';
    };
    track.addEventListener('load', showIt);
    // Some browsers don't fire 'load' reliably for <track>; nudge it.
    setTimeout(showIt, 300);
  }

  // ----------------------------------------------------------- audio playback

  playAudio(url, title) {
    this.activePlayer = 'audio';
    this.videoEl.pause();
    this.videoContainer.style.display = 'none';
    this.audioContainer.style.display = 'flex';
    if (this.playerCard) this.playerCard.classList.remove('vp-video-mode');
    this.isTranscoded = false;
    this.baseTime = 0;
    this.closeSettings();

    document.getElementById('audio-current-title').textContent = title || 'Streaming Audio';
    this.audioEl.src = url;
    this.applyPlaybackRate();
    this.audioEl.play();
    this.isPlaying = true;
    this.updatePlayButton();
  }

  playDirectPath(relPath, category, title) {
    const rawUrl = api.getRawFileUrl(relPath);
    const fname = title || relPath.split('/').pop();
    const isVideo = category === 'video' || /\.(mp4|mkv|webm|avi|mov)$/i.test(relPath);

    if (isVideo) {
      this.playVideo(rawUrl, fname, relPath);
    } else {
      this.playAudio(rawUrl, fname);
    }
  }

  // ------------------------------------------------------- transport controls

  togglePlay() {
    const el = this.getActiveElement();
    if (!el || !el.src) {
      if (this.playlist.length > 0) this.playByIndex(0);
      return;
    }
    if (el.paused) {
      el.play();
      this.isPlaying = true;
    } else {
      el.pause();
      this.isPlaying = false;
    }
    this.updatePlayButton();
    if (this.activePlayer === 'video') {
      this.flashIcon(this.isPlaying ? 'play' : 'pause');
    }
  }

  playNext() {
    if (this.playlist.length > 0) {
      const nextIndex = (this.currentIndex + 1) % this.playlist.length;
      this.playByIndex(nextIndex);
    }
  }

  playPrev() {
    if (this.playlist.length > 0) {
      const prevIndex = (this.currentIndex - 1 + this.playlist.length) % this.playlist.length;
      this.playByIndex(prevIndex);
    }
  }

  /** Back arrow: leave the media view for wherever playback was opened from. */
  goBack() {
    const el = this.getActiveElement();
    if (el) el.pause();
    // `app` is a top-level `const` in app.js, so it lives in the global
    // lexical scope and is NOT a property of `window` — probe it with
    // `typeof`, not `window.app` (which is always undefined).
    const hasApp = typeof app !== 'undefined';
    const target = (hasApp && app.previousView && app.previousView !== 'media')
      ? app.previousView
      : 'files';
    if (hasApp) app.switchView(target);
  }

  /**
   * Fully stop playback and release the server-side stream.
   *
   * Pausing a media element does NOT close its HTTP connection — the browser
   * keeps the /api/media/stream socket open to hold its buffer, which keeps the
   * ffmpeg transcode alive and the source file locked open (Windows then blocks
   * move/rename/delete with "in use by ffmpeg", and uvicorn's Ctrl+C shutdown
   * hangs waiting on the dangling response). Detaching the source and calling
   * load() aborts that request, so the server sees a client disconnect and kills
   * ffmpeg. Called whenever we leave the media view (nav change, back button, or
   * the tab being hidden/closed).
   */
  stopPlayback() {
    clearTimeout(this._hideTimer);
    this.closeSettings();
    [this.videoEl, this.audioEl].forEach(el => {
      if (!el) return;
      try {
        el.pause();
        // Drop subtitle <track>s (and revoke nothing — src is a URL) first.
        Array.from(el.querySelectorAll('track')).forEach(t => t.remove());
        el.removeAttribute('src');
        el.load();  // aborts the in-flight fetch → server disconnect → ffmpeg dies
      } catch (_) {}
    });
    this.isPlaying = false;
    this.updatePlayButton();
  }

  // ------------------------------------------------------------- volume/mute

  applyVolume(val) {
    val = Math.min(1, Math.max(0, isNaN(val) ? 0.8 : val));
    if (this.videoEl) { this.videoEl.volume = val; this.videoEl.muted = val === 0; }
    if (this.audioEl) { this.audioEl.volume = val; this.audioEl.muted = val === 0; }
    this.syncVolumeUI();
  }

  nudgeVolume(delta) {
    const v = this.videoEl;
    if (!v) return;
    this.applyVolume((v.muted ? 0 : v.volume) + delta);
  }

  toggleMute() {
    const v = this.videoEl;
    if (!v) return;
    if (v.muted || v.volume === 0) {
      v.muted = false;
      if (this.audioEl) this.audioEl.muted = false;
      if (v.volume === 0) this.applyVolume(0.5);
    } else {
      v.muted = true;
      if (this.audioEl) this.audioEl.muted = true;
    }
    this.syncVolumeUI();
  }

  syncVolumeUI() {
    const v = this.videoEl;
    if (!v) return;
    const muted = v.muted || v.volume === 0;
    const icon = muted ? 'volume-x' : (v.volume < 0.5 ? 'volume-1' : 'volume-2');
    const btn = document.getElementById('vp-btn-mute');
    if (btn) btn.innerHTML = `<i data-lucide="${icon}"></i>`;
    const shown = muted ? 0 : v.volume;
    const vpVol = document.getElementById('vp-volume');
    if (vpVol) vpVol.value = shown;
    const audioVol = document.getElementById('media-volume-bar');
    if (audioVol) audioVol.value = shown;
    if (window.lucide) lucide.createIcons();
  }

  // -------------------------------------------------------------- bookmarks

  loadBookmarks() {
    try {
      return JSON.parse(localStorage.getItem('diskpulse_video_bookmarks')) || {};
    } catch (_) {
      return {};
    }
  }

  saveBookmarks() {
    try {
      localStorage.setItem('diskpulse_video_bookmarks', JSON.stringify(this._bookmarks));
    } catch (_) {}
  }

  /** Bookmark button: save the current position, or clear it if already set. */
  toggleBookmark() {
    if (!this.videoRelPath) return;
    const path = this.videoRelPath;
    if (this._bookmarks[path]) {
      delete this._bookmarks[path];
      this.saveBookmarks();
      this.toast('Bookmark removed');
    } else {
      const t = (this.baseTime || 0) + (this.videoEl.currentTime || 0);
      if (t < 5) {
        this.toast('Play a little further before bookmarking');
        return;
      }
      this._bookmarks[path] = Math.floor(t);
      this.saveBookmarks();
      this.toast(`Bookmarked at ${this.formatTime(t)}`);
    }
    this.updateBookmarkState();
  }

  updateBookmarkState() {
    const btn = document.getElementById('vp-btn-bookmark');
    if (!btn) return;
    const has = !!(this.videoRelPath && this._bookmarks[this.videoRelPath]);
    btn.classList.toggle('vp-active', has);
    btn.innerHTML = `<i data-lucide="${has ? 'bookmark-check' : 'bookmark'}"></i>`;
    if (window.lucide) lucide.createIcons();
  }

  /** Cast via the Remote Playback API when the browser offers it.
   *
   *  The cast device fetches the media URL itself, so when this page was
   *  opened on localhost we first re-point the source at the server's LAN
   *  address — `localhost` is unreachable from anywhere else on the network,
   *  which is what made casts fail to start playing. */
  async promptCast() {
    const el = this.getActiveElement();
    if (!el || !el.remote || typeof el.remote.prompt !== 'function') {
      this.toast('Casting is not supported in this browser');
      return;
    }
    if (!el.currentSrc) {
      this.toast('Play something first, then cast');
      return;
    }
    try {
      await this.ensureCastOrigin();
      if (this._castOrigin) this.switchSrcOrigin(el, this._castOrigin);
    } catch (err) {
      console.error('Cast setup failed:', err);
    }
    el.remote.prompt().catch(() => {});
  }

  /** Resolve (once) the LAN origin cast devices should fetch media from.
   *  Only needed when the page itself is served from a loopback address. */
  async ensureCastOrigin() {
    if (this._castOrigin) return;
    const host = window.location.hostname;
    if (host !== 'localhost' && host !== '127.0.0.1' && host !== '::1') return;
    const info = await api.getServerInfo();
    if (!info || !info.host || info.host === '127.0.0.1') {
      this.toast('Server has no LAN address — open DiskPulse via its network IP to cast');
      return;
    }
    this._castOrigin = `http://${info.host}:${info.port || window.location.port}`;
  }

  /** Re-point the playing element at `origin` without losing its position. */
  switchSrcOrigin(el, origin) {
    const src = el.currentSrc || el.src;
    if (!src || !src.startsWith(window.location.origin)) return;
    const t = el.currentTime || 0;
    const wasPlaying = !el.paused;
    el.addEventListener('loadedmetadata', () => {
      try { el.currentTime = t; } catch (_) {}
      if (wasPlaying) el.play().catch(() => {});
    }, { once: true });
    el.src = src.replace(window.location.origin, origin);
    el.load();
  }

  /** Remote playback ended: drop the LAN origin and resume locally. */
  endCastMode() {
    if (!this._castOrigin) return;
    this._castOrigin = null;
    if (this.activePlayer !== 'video' || !this.videoRelPath) return;
    const v = this.videoEl;
    this.loadVideoSource({
      useStream: this.isTranscoded,
      audioIdx: this.currentAudioIdx,
      startTime: (this.baseTime || 0) + (v.currentTime || 0),
      autoplay: !v.paused,
    });
  }

  toast(msg) {
    // Same const-global caveat as goBack(): `window.fileManager` is always
    // undefined, which used to swallow every player toast silently.
    if (typeof fileManager !== 'undefined' && typeof fileManager.showToast === 'function') {
      fileManager.showToast(msg);
    }
  }

  // ------------------------------------------------------- overlay auto-hide

  pokeControls() {
    if (!this.videoContainer) return;
    this.videoContainer.classList.remove('vp-hidden', 'vp-cursor-hidden');
    clearTimeout(this._hideTimer);
    if (this.isPlaying && !this._scrubbing && !this.isMenuOpen()) {
      this._hideTimer = setTimeout(() => this.hideControls(), this.HIDE_DELAY_MS);
    }
  }

  hideControls() {
    if (!this.isPlaying || this._scrubbing || this.isMenuOpen()) return;
    this.videoContainer.classList.add('vp-hidden', 'vp-cursor-hidden');
  }

  // -------------------------------------------------------- visual feedback

  /** Transient center play/pause flash (YouTube-style). */
  flashIcon(name) {
    const flash = document.getElementById('vp-flash');
    if (!flash) return;
    flash.innerHTML = `<i data-lucide="${name}"></i>`;
    if (window.lucide) lucide.createIcons();
    flash.classList.remove('vp-flash-on');
    void flash.offsetWidth; // restart the animation
    flash.classList.add('vp-flash-on');
  }

  showSeekIndicator(side) {
    const el = document.getElementById(side === 'left' ? 'vp-ind-left' : 'vp-ind-right');
    if (!el) return;
    el.classList.remove('vp-ind-on');
    void el.offsetWidth;
    el.classList.add('vp-ind-on');
  }

  // ------------------------------------------------------- scrub bar & times

  paintScrub(frac) {
    const pct = `${(Math.min(1, Math.max(0, frac)) * 100).toFixed(3)}%`;
    const fill = document.getElementById('vp-seek-fill');
    const thumb = document.getElementById('vp-seek-thumb');
    if (fill) fill.style.width = pct;
    if (thumb) thumb.style.left = pct;
  }

  labelScrubTime(frac) {
    const dur = this.effectiveDuration();
    const curEl = document.getElementById('vp-cur');
    if (curEl && dur > 0) curEl.textContent = this.formatTime(frac * dur);
  }

  setOverlayTimes(cur, dur) {
    const curEl = document.getElementById('vp-cur');
    const durEl = document.getElementById('vp-dur');
    if (curEl) curEl.textContent = this.formatTime(cur);
    if (durEl) durEl.textContent = this.formatTime(dur);
  }

  /** Hover preview bubble: time label always, ffmpeg frame when available. */
  showPreview(frac) {
    const preview = document.getElementById('vp-preview');
    const bar = document.getElementById('vp-seek');
    const dur = this.effectiveDuration();
    if (!preview || !bar || dur <= 0) return;

    const t = frac * dur;
    const timeEl = document.getElementById('vp-preview-time');
    if (timeEl) timeEl.textContent = this.formatTime(t);

    // Clamp the bubble inside the bar's width.
    preview.classList.add('vp-preview-on');
    const barW = bar.getBoundingClientRect().width;
    const pw = preview.offsetWidth || 172;
    const x = Math.max(pw / 2 + 2, Math.min(barW - pw / 2 - 2, frac * barW));
    preview.style.left = `${x}px`;

    this.requestThumb(t);
  }

  hidePreview() {
    document.getElementById('vp-preview')?.classList.remove('vp-preview-on');
    this._thumbPending = null;
  }

  /** Fetch (and cache per second) a preview frame; degrade gracefully to a
   *  time-only bubble when the server can't produce frames. */
  requestThumb(t) {
    if (!this.videoRelPath || this._thumbsDead) return;
    const key = Math.round(t);
    const img = document.getElementById('vp-preview-img');
    const preview = document.getElementById('vp-preview');
    if (!img || !preview) return;

    if (this._thumbCache.has(key)) {
      img.src = this._thumbCache.get(key);
      preview.classList.remove('vp-preview-noimg');
      return;
    }

    if (this._thumbPending === key) return;
    const now = performance.now();
    if (now - this._thumbLastReq < 120) return; // throttle while sliding
    this._thumbLastReq = now;
    this._thumbPending = key;

    fetch(api.getMediaThumbUrl(this.videoRelPath, key))
      .then(r => {
        if (!r.ok) throw new Error(`thumb ${r.status}`);
        return r.blob();
      })
      .then(blob => {
        if (this._thumbPending !== key) return; // user already moved on
        const url = URL.createObjectURL(blob);
        this._thumbCache.set(key, url);
        if (this._thumbCache.size > 60) {
          const [oldKey, oldUrl] = this._thumbCache.entries().next().value;
          this._thumbCache.delete(oldKey);
          URL.revokeObjectURL(oldUrl);
        }
        img.src = url;
        preview.classList.remove('vp-preview-noimg');
      })
      .catch(() => {
        this._thumbsDead = true;
        preview.classList.add('vp-preview-noimg');
      });
  }

  resetThumbCache() {
    for (const url of this._thumbCache.values()) URL.revokeObjectURL(url);
    this._thumbCache.clear();
    this._thumbPending = null;
    this._thumbsDead = false;
    this.hidePreview();
  }

  // ------------------------------------------------------------ UI sync

  updatePlayButton() {
    const icon = this.isPlaying ? 'pause' : 'play';
    for (const id of ['media-btn-play', 'vp-btn-play']) {
      const btn = document.getElementById(id);
      if (btn) btn.innerHTML = `<i data-lucide="${icon}"></i>`;
    }
    if (window.lucide) lucide.createIcons();
  }

  updateTime() {
    const el = this.getActiveElement();
    if (!el) return;
    if (this._scrubbing) return;  // don't fight the user's drag

    const base = (this.activePlayer === 'video') ? (this.baseTime || 0) : 0;
    const cur = base + (el.currentTime || 0);
    const dur = this.effectiveDuration();

    // Audio card controls
    const curEl = document.getElementById('media-current-time');
    const durEl = document.getElementById('media-duration');
    const seekBar = document.getElementById('media-seek-bar');
    if (curEl) curEl.textContent = this.formatTime(cur);
    if (durEl) durEl.textContent = this.formatTime(dur);
    if (seekBar && dur > 0) seekBar.value = (cur / dur) * 100;

    // Video overlay
    if (this.activePlayer === 'video') {
      this.setOverlayTimes(cur, dur);
      this.paintScrub(dur > 0 ? cur / dur : 0);
    }
  }

  formatTime(secs) {
    if (!secs || !isFinite(secs)) secs = 0;
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = Math.floor(secs % 60);
    const mm = (h > 0 && m < 10) ? `0${m}` : `${m}`;
    const ss = s < 10 ? `0${s}` : `${s}`;
    return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
  }

  initVisualizer() {
    if (!this.canvas) return;
    const ctx = this.canvas.getContext('2d');
    let phase = 0;

    const draw = () => {
      requestAnimationFrame(draw);
      ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

      if (this.activePlayer !== 'audio' || !this.isPlaying) return;

      const width = this.canvas.width = this.canvas.offsetWidth;
      const height = this.canvas.height = this.canvas.offsetHeight;
      const bars = 48;
      const barWidth = width / bars;

      phase += 0.05;
      for (let i = 0; i < bars; i++) {
        const h = Math.abs(Math.sin(phase + i * 0.25)) * (height * 0.6) + 10;
        const x = i * barWidth;
        const y = height - h;

        const grad = ctx.createLinearGradient(0, y, 0, height);
        grad.addColorStop(0, '#8b5cf6');
        grad.addColorStop(0.5, '#00f2fe');
        grad.addColorStop(1, '#10b981');

        ctx.fillStyle = grad;
        ctx.fillRect(x + 2, y, barWidth - 4, h);
      }
    };
    draw();
  }
}

const mediaPlayer = new WebMediaPlayer();

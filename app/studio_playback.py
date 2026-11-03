"""Editor transport driven by the decoded song's clock, without a movie render."""
from PySide6.QtCore import QTimer, QUrl, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from app.studio_authoring import StudioAuthoring


class StudioPlayback(StudioAuthoring):
    def _init_playback(self):
        self._playback_source = ''
        self._playback_seeking = False
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(.7)
        self._preview_player = QMediaPlayer(self)
        self._preview_player.setAudioOutput(self._audio_output)
        self._preview_player.positionChanged.connect(self._playback_position)
        self._preview_player.playbackStateChanged.connect(self._playback_state)
        self._preview_player.mediaStatusChanged.connect(self._playback_status)
        self._preview_player.errorOccurred.connect(self._playback_error)
        self._playback_poll = QTimer(self)
        self._playback_poll.setInterval(40)
        self._playback_poll.timeout.connect(lambda: self._playback_position(self._preview_player.position()))
        self._state.update(playbackPlaying=False, playbackReady=False, playbackError='',
                           playbackMuted=False, timelineSilent=False)

    def _sync_playback_source(self):
        source = self._state['audioPath']
        if self._state['busy']:
            self.pausePreview()
        if source != self._playback_source:
            self._playback_seeking = True
            try:
                self._preview_player.stop()
                self._playback_source = source
                self._state.update(playbackPlaying=False, playbackReady=False, playbackError='')
                self._preview_player.setSource(QUrl.fromLocalFile(source) if source else QUrl())
            finally:
                self._playback_seeking = False

    def _publish_playback(self):
        # Transport must not recalculate content hashes, invalidate renders, or dirty the project.
        # Changing source emits intermediate stop/load signals. The outer
        # operation publishes the complete new source once it is ready.
        if self._playback_seeking:
            return
        self._project_position()
        self._state['previewRevision'] += 1
        self.stateChanged.emit()

    def _playback_position(self, position):
        if not self._playback_seeking and self._state['playbackPlaying']:
            position = min(self._state['durationMs'], max(0, position))
            if position == self._state['positionMs']:
                return
            self._state['positionMs'] = position
            self._publish_playback()

    def _playback_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._state['playbackPlaying'] = playing
        if playing:
            self._playback_poll.start()
        else:
            self._playback_poll.stop()
        self._publish_playback()

    def _playback_status(self, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            if not self._state['playbackReady']:
                self._state['playbackReady'] = True
                self._seek_playback(self._state['positionMs'])
                self._publish_playback()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._state.update(playbackPlaying=False, positionMs=self._state['durationMs'])
            self._publish_playback()

    def _playback_error(self, error, message):
        self._state.update(playbackPlaying=False, playbackReady=False,
                           playbackError='歌曲播放不可用：' + message)
        self._publish_playback()

    def _seek_playback(self, position):
        self._playback_seeking = True
        try:
            self._preview_player.setPosition(round(position))
        finally:
            self._playback_seeking = False

    @Slot()
    def playPreview(self):
        if self._state['busy'] or not self._state['audioPath']:
            return
        self._sync_playback_source()
        if self._state['positionMs'] >= self._state['durationMs'] - 1:
            self._state['positionMs'] = 0
        self._seek_playback(self._state['positionMs'])
        self._preview_player.play()

    @Slot()
    def pausePreview(self):
        if self._preview_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._preview_player.pause()

    @Slot()
    def togglePreview(self):
        if self._state['playbackPlaying']:
            self.pausePreview()
        else:
            self.playPreview()

    @Slot()
    def togglePreviewMute(self):
        self._audio_output.setMuted(not self._audio_output.isMuted())
        self._state['playbackMuted'] = self._audio_output.isMuted()
        self._publish_playback()

    def _shutdown_playback(self):
        self._playback_poll.stop()
        self._playback_seeking = True
        self._preview_player.stop()
        self._preview_player.setSource(QUrl())

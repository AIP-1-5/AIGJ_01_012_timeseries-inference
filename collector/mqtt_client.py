"""MQTT 구독과 저장 루프. 끊기면 스스로 다시 붙고, 조용히 끊긴 것도 감시한다 (research.md R5)."""

import logging
import signal
import socket
import sqlite3
import threading
import time
from collections import Counter

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from . import __version__
from .parser import find_source, parse_message
from .timeutil import utcnow

log = logging.getLogger("collector")

SUMMARY_INTERVAL_SECONDS = 60
MAX_CONSECUTIVE_STORE_ERRORS = 10


def should_reconnect(connected, last_message_at, now, stale_seconds):
    """연결된 상태인데 stale_seconds 동안 메시지가 없으면 True (조용한 끊김)."""
    return connected and last_message_at is not None and now - last_message_at >= stale_seconds


class Collector:
    def __init__(self, config, store, clock=time.monotonic):
        self.config = config
        self.store = store
        self.clock = clock
        self.connected = False
        self.last_message_at = None
        self.exit_code = 0
        self.stats = Counter()
        self._stats_lock = threading.Lock()
        self._stop = threading.Event()
        self._failed_attempts = 0
        self._store_errors = 0

    # ---- 클라이언트 구성 ----

    def build_client(self):
        broker = self.config.broker
        username, password = broker.credentials()
        client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=broker.client_id,
            clean_session=broker.clean_session,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(username, password)
        client.reconnect_delay_set(min_delay=broker.reconnect_min_seconds, max_delay=broker.reconnect_max_seconds)
        client.on_connect = self.on_connect
        client.on_connect_fail = self.on_connect_fail
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        return client

    # ---- 콜백 ----

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self._record_failed_attempt(f"connack={reason_code}")
            return
        self.connected = True
        self.last_message_at = self.clock()
        detail = f"failed_attempts={self._failed_attempts}" if self._failed_attempts else None
        self._failed_attempts = 0
        self._event("connect", detail)
        log.info("브로커 접속: %s:%s", self.config.broker.host, self.config.broker.port)
        for source in self.config.sources:
            client.subscribe(source.topic, qos=self.config.broker.qos)
            self._event("subscribe", f"{source.name}: {source.topic}")

    def on_connect_fail(self, client, userdata):
        self._record_failed_attempt("connect failed")

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected:
            self._event("disconnect", str(reason_code))
            log.warning("브로커 연결 끊김: %s", reason_code)
        self.connected = False

    def on_message(self, client, userdata, msg):
        self.handle_message(msg.topic, msg.payload, utcnow())

    # ---- 처리 ----

    def handle_message(self, topic, payload, received_at):
        self.last_message_at = self.clock()
        source = find_source(self.config.sources, topic)
        if source is None:
            self._reject(topic, "no_matching_source", payload)
            return
        result = parse_message(topic, payload, source, received_at)
        for reason in result.rejects:
            self._reject(topic, reason, payload)
        if not result.rows:
            return
        try:
            inserted, duplicate = self.store.insert_message(result.rows)
        except sqlite3.Error as exc:
            self._store_errors += 1
            log.error("저장 실패(%d회 연속): %s", self._store_errors, exc)
            try:
                self.store.add_event("store_error", str(exc))
            except sqlite3.Error:
                pass
            if self._store_errors >= MAX_CONSECUTIVE_STORE_ERRORS:
                log.critical("저장 실패가 계속된다. 종료해서 systemd가 다시 실행하게 한다")
                self.exit_code = 1
                self._stop.set()
            return
        self._store_errors = 0
        with self._stats_lock:
            self.stats["messages"] += 1
            self.stats["rows"] += inserted
            if duplicate:
                self.stats["duplicates"] += 1

    def _reject(self, topic, reason, payload):
        with self._stats_lock:
            self.stats["rejects"] += 1
        try:
            self.store.add_reject(topic, reason, payload)
        except sqlite3.Error as exc:
            log.error("실패 메시지 기록 실패: %s", exc)

    def _record_failed_attempt(self, detail):
        # 끊긴 동안의 재시도는 첫 번째만 기록하고, 나머지는 성공 시 횟수로 합친다
        if self._failed_attempts == 0:
            self._event("reconnect_attempt", detail)
        self._failed_attempts += 1

    def _event(self, kind, detail=None):
        try:
            self.store.add_event(kind, detail)
        except sqlite3.Error as exc:
            log.error("사건 기록 실패(%s): %s", kind, exc)

    def _log_summary(self):
        with self._stats_lock:
            stats, self.stats = self.stats, Counter()
        log.info(
            "1분 요약: 메시지 %d건, 저장 %d행, 중복 %d건, 실패 %d건, 연결 %s",
            stats["messages"], stats["rows"], stats["duplicates"], stats["rejects"],
            "됨" if self.connected else "끊김",
        )

    # ---- 실행 ----

    def stop(self, *_):
        self._stop.set()

    def run(self):
        broker = self.config.broker
        client = self.build_client()
        self._event("start", f"v{__version__} host={socket.gethostname()} client_id={broker.client_id} config={self.config.path}")
        log.info("수집 시작: client_id=%s, 저장소=%s", broker.client_id, self.config.storage_path)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        client.connect_async(broker.host, broker.port, keepalive=broker.keepalive_seconds)
        client.loop_start()
        stale_seconds = broker.stale_after_minutes * 60
        next_summary = self.clock() + SUMMARY_INTERVAL_SECONDS
        try:
            while not self._stop.wait(1):
                now = self.clock()
                if should_reconnect(self.connected, self.last_message_at, now, stale_seconds):
                    self._event("stale_reconnect", f"{broker.stale_after_minutes}분 동안 메시지 없음")
                    log.warning("%s분 동안 메시지가 없어 다시 접속한다", broker.stale_after_minutes)
                    self.last_message_at = now
                    try:
                        client.reconnect()
                    except (OSError, ValueError) as exc:
                        # 실패해도 네트워크 루프가 재시도 간격대로 다시 붙는다
                        log.warning("재접속 실패, 자동 재시도에 맡긴다: %s", exc)
                if now >= next_summary:
                    self._log_summary()
                    next_summary = now + SUMMARY_INTERVAL_SECONDS
        finally:
            client.disconnect()
            client.loop_stop()
            self._event("stop", f"exit_code={self.exit_code}")
            log.info("수집 종료 (exit_code=%d)", self.exit_code)
        return self.exit_code

"""Reglas de alerta del streaming (Parte D), sin dependencia de Kafka.

El detector recibe eventos ya deserializados en el orden en que los entrega
el tópico. El productor particiona por frontera_id, así que el orden está
garantizado **por frontera** y no entre fronteras: todo el estado del
detector es por frontera, por eso el resultado no depende de cómo se
intercalen las particiones.

Reglas (README, Parte D), exactas:
- FRONTERA_SIN_REPORTE: 3 intervalos consecutivos de 15 min sin medición en
  la rejilla del día (96 intervalos desde 2026-06-01T00:00-05:00). Una sola
  alerta por caída; ts = el del tercer intervalo ausente. Se detecta cuando
  la frontera vuelve a reportar después del hueco y, al cerrar, para las
  caídas que siguen abiertas al final del día.
- CONSUMO_FUERA_DE_BANDA: kwh estrictamente mayor que max_kwh de
  limites_frontera.csv; ts = el de la medición.
- LECTURA_RETROACTIVA: ts_medicion anterior (estricto) a la última ya
  procesada para esa frontera; ts = el de la medición tardía.

Decisiones:
- Un evento con evento_id ya visto se ignora: Kafka entrega "al menos una
  vez", y una reentrega no puede convertirse en una LECTURA_RETROACTIVA falsa.
- Un evento inválido (sin campos, kwh no numérico, ts ilegible, frontera sin
  límites) se rechaza con motivo y no cuenta como medición.
- Un ts sin zona se interpreta en hora de Colombia (B1). Toda alerta sale
  con offset -05:00.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

BOGOTA = timezone(timedelta(hours=-5))
INICIO_DIA = datetime(2026, 6, 1, tzinfo=BOGOTA)
INTERVALOS = 96
PASO = timedelta(minutes=15)
UMBRAL_CAIDA = 3
RE_FRONTERA = re.compile(r"^CO\d{10}$")

SIN_REPORTE = "FRONTERA_SIN_REPORTE"
FUERA_DE_BANDA = "CONSUMO_FUERA_DE_BANDA"
RETROACTIVA = "LECTURA_RETROACTIVA"


class EventoInvalido(ValueError):
    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(frozen=True, order=True)
class Alerta:
    ts: datetime
    tipo: str
    frontera_id: str

    def como_dict(self) -> dict:
        return {"tipo": self.tipo, "frontera_id": self.frontera_id,
                "ts": self.ts.astimezone(BOGOTA).isoformat()}


@dataclass
class _Estado:
    ultimo_ts: datetime | None = None
    ultimo_intervalo: int = -1          # mayor intervalo de la rejilla con medición


@dataclass
class Detector:
    limites: dict[str, Decimal]          # frontera_id → max_kwh (universo de fronteras)
    inicio: datetime = INICIO_DIA
    intervalos: int = INTERVALOS
    umbral: int = UMBRAL_CAIDA
    alertas: set[Alerta] = field(default_factory=set)
    rechazos: Counter = field(default_factory=Counter)
    procesados: int = 0
    duplicados: int = 0
    fuera_de_rejilla: int = 0
    _estado: dict[str, _Estado] = field(default_factory=dict)
    _ids: set[str] = field(default_factory=set)

    # ------------------------------------------------------------ eventos --
    def procesar(self, evento: object) -> list[Alerta]:
        """Procesa un evento; devuelve las alertas nuevas que dispara."""
        try:
            eid, frontera, ts, kwh = self._validar(evento)
        except EventoInvalido as e:
            self.rechazos[e.motivo] += 1
            return []
        if eid in self._ids:
            self.duplicados += 1
            return []
        self._ids.add(eid)
        self.procesados += 1
        nuevas: list[Alerta] = []
        est = self._estado.setdefault(frontera, _Estado())

        if est.ultimo_ts is not None and ts < est.ultimo_ts:
            nuevas.append(Alerta(ts, RETROACTIVA, frontera))
        if kwh > self.limites[frontera]:
            nuevas.append(Alerta(ts, FUERA_DE_BANDA, frontera))

        k = self._intervalo(ts)
        if k is None:
            self.fuera_de_rejilla += 1
        elif k > est.ultimo_intervalo:
            nuevas += self._caida(frontera, est.ultimo_intervalo, k)
            est.ultimo_intervalo = k
        if est.ultimo_ts is None or ts > est.ultimo_ts:
            est.ultimo_ts = ts
        return self._registrar(nuevas)

    def cerrar(self) -> list[Alerta]:
        """Fin del día: caídas abiertas, incluidas fronteras que nunca reportaron."""
        nuevas: list[Alerta] = []
        for frontera in sorted(self.limites):
            est = self._estado.get(frontera, _Estado())
            nuevas += self._caida(frontera, est.ultimo_intervalo, self.intervalos)
        return self._registrar(nuevas)

    def resultado(self) -> list[dict]:
        return [a.como_dict() for a in sorted(self.alertas)]

    # ---------------------------------------------------------- internos --
    def _caida(self, frontera: str, ultimo: int, siguiente: int) -> list[Alerta]:
        """Hueco entre el intervalo `ultimo` (con medición) y `siguiente`."""
        if siguiente - ultimo - 1 >= self.umbral:
            return [Alerta(self.inicio + PASO * (ultimo + self.umbral), SIN_REPORTE, frontera)]
        return []

    def _intervalo(self, ts: datetime) -> int | None:
        delta = ts - self.inicio
        k, resto = divmod(delta, PASO)
        if resto or not 0 <= k < self.intervalos:
            return None
        return int(k)

    def _registrar(self, nuevas: list[Alerta]) -> list[Alerta]:
        frescas = [a for a in nuevas if a not in self.alertas]
        self.alertas.update(frescas)
        return frescas

    def _validar(self, evento: object) -> tuple[str, str, datetime, Decimal]:
        if not isinstance(evento, dict):
            raise EventoInvalido("EVENTO_NO_ES_OBJETO")
        eid, frontera = evento.get("evento_id"), evento.get("frontera_id")
        if not isinstance(eid, str) or not eid.strip():
            raise EventoInvalido("SIN_EVENTO_ID")
        if not isinstance(frontera, str) or not RE_FRONTERA.match(frontera):
            raise EventoInvalido("FRONTERA_INVALIDA")
        if frontera not in self.limites:
            raise EventoInvalido("FRONTERA_SIN_LIMITES")
        crudo = str(evento.get("ts_medicion")).strip()
        if crudo[-1:] in ("Z", "z"):  # fromisoformat solo acepta "Z" desde Python 3.11
            crudo = crudo[:-1] + "+00:00"
        try:
            ts = datetime.fromisoformat(crudo)
        except ValueError:
            raise EventoInvalido("TS_ILEGIBLE") from None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=BOGOTA)
        kwh = evento.get("kwh")
        if isinstance(kwh, bool) or not isinstance(kwh, (int, float, str)):
            raise EventoInvalido("KWH_NO_NUMERICO")
        try:
            valor = Decimal(str(kwh))
        except InvalidOperation:
            raise EventoInvalido("KWH_NO_NUMERICO") from None
        if not valor.is_finite():
            raise EventoInvalido("KWH_NO_NUMERICO")
        return eid, frontera, ts, valor

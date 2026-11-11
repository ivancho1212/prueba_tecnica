"""Pipeline batch (`make run`). Orquesta maestros → consumos → facturación →
reportes. Idempotente: correrlo N veces deja el mismo estado y las mismas salidas.

Uso: .venv/bin/python -m bia.pipeline
"""
from __future__ import annotations

import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from bia import config, db, reportes
from bia.classification import Clasificacion
from bia.crypto import Cifrador
from bia.ingesta import archivos, consumos, facturacion, maestros
from bia.ingesta.carga import Cargador
from bia.logging_json import configurar, log

UMBRAL_RECHAZO_ESTRUCTURAL = 0.5   # > 50 % de filas malformadas ⇒ el archivo se rechaza entero


class Cronometro:
    def __init__(self):
        self.etapas: dict[str, int] = {}
        self._t = time.monotonic()

    def marcar(self, etapa: str) -> int:
        ahora = time.monotonic()
        ms = int((ahora - self._t) * 1000)
        self.etapas[etapa] = ms
        self._t = ahora
        return ms


def main() -> int:
    t_inicio = time.monotonic()
    inicio_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    corrida = uuid.uuid4()
    cfg = config.cargar()
    clasif = Clasificacion.cargar()
    clasif.verificar_vistas_analista()
    logger = configurar(cfg.log_dir / "pipeline.log", str(corrida), "batch",
                        clasif.campos_prohibidos_en_logs(), cfg.log_level)
    log(logger, "inicio", data_dir=str(cfg.data_dir))
    crono = Cronometro()
    cif = Cifrador(cfg.enc_key, cfg.hmac_key)
    conn = db.conectar(cfg.dsn_rol("ingesta", "bia_batch"), intentos=10)
    cargador = Cargador(conn, corrida, cif)
    cargador.bitacora("pipeline", "inicio", "INFO", detalle_inicio=inicio_iso)
    metrics: dict = {"corrida_id": str(corrida), "inicio": inicio_iso, "archivos": [], "rechazos_por_motivo": {}}
    dq: dict = {k: 0 for k in reportes.LLAVES_DQ}
    dq["horas_sin_lectura_valida_por_frontera"] = {}

    def acumular_rechazos(rs):
        for r in rs:
            metrics["rechazos_por_motivo"][r.regla] = metrics["rechazos_por_motivo"].get(r.regla, 0) + 1

    try:
        # ------------------------------------------------------------ maestros
        dir_m = cfg.data_dir / "maestros"
        clientes_ok: set[str] = set()
        for nombre, procesador in (("clientes.csv", None), ("fronteras.csv", None), ("limites_frontera.csv", None)):
            ruta = dir_m / nombre
            if not ruta.exists():
                log(logger, "maestro_ausente", logging.WARNING, archivo=nombre)
                continue
            a = archivos.descubrir(dir_m, nombre)[0]
            origen = nombre.split(".")[0].replace("limites_frontera", "limites")
            estado = cargador.registrar_archivo(a, origen, None)
            t0 = time.monotonic()
            if nombre == "clientes.csv":
                res = maestros.procesar_clientes(a, cif, clasif)
                clientes_ok = {c.cliente_id for c in res.filas}
                cargadas = cargador.upsert_clientes(res.filas, estado.archivo_id)
                dq["clientes_con_pii_en_texto_libre"] = res.clientes_con_pii_texto_libre
            elif nombre == "fronteras.csv":
                res = maestros.procesar_fronteras(a, clientes_ok)
                cargadas = cargador.upsert_fronteras(res.filas, estado.archivo_id)
            else:
                res = maestros.procesar_limites(a, cargador.fronteras_en_maestro())
                cargadas = cargador.upsert_limites(res.filas, estado.archivo_id)
            cargador.cuarentena(origen, a.sha256, res.rechazos)
            acumular_rechazos(res.rechazos)
            est = "OK" if not res.rechazos else ("RECHAZADO" if not res.filas else "PARCIAL")
            cargador.cerrar_archivo(estado.archivo_id, res.filas_leidas, cargadas, len(res.rechazos), est)
            conn.commit()
            ms = int((time.monotonic() - t0) * 1000)
            cargador.bitacora("maestros", "archivo_procesado", "OK" if est == "OK" else "PARCIAL", a.nombre,
                              cargadas, ms, leidas=res.filas_leidas, rechazadas=len(res.rechazos),
                              pii_texto_libre=getattr(res, "clientes_con_pii_texto_libre", None))
            log(logger, "maestro_procesado", archivo=a.nombre, leidas=res.filas_leidas, cargadas=cargadas,
                rechazadas=len(res.rechazos), estado=est, ms=ms)
            metrics["archivos"].append({"archivo": a.nombre, "origen": origen, "leidas": res.filas_leidas,
                                        "cargadas": cargadas, "rechazadas": len(res.rechazos), "estado": est})
        crono.marcar("maestros")
        fronteras_maestro = cargador.fronteras_en_maestro()

        # ------------------------------------------------------------ consumos
        dir_c = cfg.data_dir / "consumos"
        crudos = archivos.descubrir(dir_c, "*.csv")
        log(logger, "descubrimiento", directorio=str(dir_c), archivos=[a.nombre for a in crudos])
        todas: list[consumos.Lectura] = []
        rechazos_por_archivo: dict[str, list[consumos.Rechazo]] = {}
        estados: dict[str, object] = {}
        archivo_ids: dict[str, int] = {}
        leidas_total = 0
        orden = 0
        for a in crudos:
            t0 = time.monotonic()
            try:
                fmt = archivos.detectar_formato(a.contenido)
            except archivos.ArchivoIlegible as e:
                estado = cargador.registrar_archivo(a, "consumos", None)
                r = consumos.Rechazo(a.nombre, None, "ARCHIVO_CORRUPTO", str(e), None, {})
                cargador.cuarentena("consumos", a.sha256, [r]); acumular_rechazos([r])
                cargador.cerrar_archivo(estado.archivo_id, 0, 0, 1, "RECHAZADO"); conn.commit()
                cargador.bitacora("consumos", "archivo_rechazado", "ERROR", a.nombre, 0, detalle_motivo=str(e))
                log(logger, "archivo_rechazado", logging.WARNING, archivo=a.nombre, motivo=str(e))
                metrics["archivos"].append({"archivo": a.nombre, "origen": "consumos", "leidas": 0, "cargadas": 0,
                                            "rechazadas": 1, "estado": "RECHAZADO", "motivo": str(e)})
                continue
            estado = cargador.registrar_archivo(a, "consumos", fmt)
            res = consumos.parsear_archivo(a, fmt, orden)
            orden += len(res.lecturas)
            leidas_total += res.filas_leidas
            dq["registros_mwh_convertidos"] += res.mwh_convertidos
            estructurales = len(res.rechazos)
            if res.filas_leidas and (res.truncado and estructurales / max(res.filas_leidas, 1) > UMBRAL_RECHAZO_ESTRUCTURAL
                                     or estructurales / res.filas_leidas > UMBRAL_RECHAZO_ESTRUCTURAL):
                r = consumos.Rechazo(a.nombre, None, "ARCHIVO_CORRUPTO",
                                     f"{estructurales} de {res.filas_leidas} filas malformadas", None, {})
                cargador.cuarentena("consumos", a.sha256, [r] + res.rechazos); acumular_rechazos([r] + res.rechazos)
                cargador.cerrar_archivo(estado.archivo_id, res.filas_leidas, 0, estructurales + 1, "RECHAZADO")
                conn.commit()
                cargador.bitacora("consumos", "archivo_rechazado", "ERROR", a.nombre, 0, detalle_motivo=r.motivo)
                log(logger, "archivo_rechazado", logging.WARNING, archivo=a.nombre, motivo=r.motivo)
                metrics["archivos"].append({"archivo": a.nombre, "origen": "consumos", "leidas": res.filas_leidas,
                                            "cargadas": 0, "rechazadas": estructurales + 1, "estado": "RECHAZADO"})
                continue
            # fronteras que no existen en el maestro: no se pueden cargar (FK) → cuarentena
            desconocidas = [l for l in res.lecturas if l.frontera_id not in fronteras_maestro]
            for l in desconocidas:
                res.rechazos.append(consumos.Rechazo(a.nombre, l.fila, "FRONTERA_DESCONOCIDA",
                                                     "frontera_id no existe en el maestro", l.frontera_id,
                                                     {"frontera_id": l.frontera_id, "ts": l.ts_local.isoformat(), "valor": l.valor_crudo}))
            res.lecturas = [l for l in res.lecturas if l.frontera_id in fronteras_maestro]
            todas.extend(res.lecturas)
            rechazos_por_archivo[a.nombre] = res.rechazos
            estados[a.nombre] = estado
            archivo_ids[a.nombre] = estado.archivo_id
            metrics["archivos"].append({"archivo": a.nombre, "origen": "consumos", "leidas": res.filas_leidas,
                                        "formato": {"encoding": fmt.encoding, "separador": fmt.separador,
                                                    "ts_utc": fmt.ts_es_utc, "decimal_coma": fmt.decimal_coma,
                                                    "unidad_defecto": fmt.unidad_por_defecto},
                                        "ms_parseo": int((time.monotonic() - t0) * 1000)})
            log(logger, "archivo_parseado", archivo=a.nombre, leidas=res.filas_leidas, lecturas=len(res.lecturas),
                rechazos_estructurales=len(res.rechazos), mwh_convertidos=res.mwh_convertidos,
                ya_cargado=estado.ya_cargado, formato=fmt.encoding + "/" + fmt.separador)
        crono.marcar("parseo_consumos")

        # B3 → B4 → B5 sobre el conjunto completo (todos los archivos presentes)
        dd = consumos.deduplicar(todas)
        va = consumos.validar(dd.ganadoras)
        meses = consumos.meses_presentes(todas)
        por_frontera = consumos.huecos(va.validas, fronteras_maestro, meses)
        dq.update({
            "filas_leidas": leidas_total,
            "duplicados_exactos": dd.duplicados_exactos,
            "duplicados_conflictivos": dd.duplicados_conflictivos,
            "registros_negativos": va.negativos,
            "registros_nulos": va.nulos,
            "registros_outlier": va.outliers,
            "correcciones_tardias_aplicadas": dd.correcciones_tardias,
            "horas_sin_lectura_valida_total": sum(por_frontera.values()),
            "horas_sin_lectura_valida_por_frontera": por_frontera,
        })
        # rechazos de reglas B3/B4 → cuarentena, agrupados por archivo de origen
        for l, regla in dd.descartadas + va.descartadas:
            rechazos_por_archivo.setdefault(l.archivo, []).append(consumos.Rechazo(
                l.archivo, l.fila, regla, {"DUPLICADO_EXACTO": "copia exacta de otra lectura",
                                           "DUPLICADO_CONFLICTIVO": "misma clave y versión, valor distinto; perdió por orden de archivo (B3)",
                                           "B3_SUPERADA_POR_VERSION": "reemplazada por una lectura de mayor version_lectura (dato tardío)",
                                           "B4_NULO": "valor nulo o vacío", "B4_NEGATIVO": "valor negativo",
                                           "B4_OUTLIER": "valor > 1.000.000 kWh"}[regla],
                f"{l.frontera_id}|{l.ts_local.isoformat()}",
                {"frontera_id": l.frontera_id, "ts_local": l.ts_local.isoformat(), "valor": l.valor_crudo,
                 "version": l.version, "unidad": l.unidad_origen}))
        crono.marcar("reglas_calidad")

        # carga por archivo: solo se reescriben filas de archivos nuevos o cambiados
        validas_por_archivo: dict[str, list[consumos.Lectura]] = {}
        for l in va.validas:
            validas_por_archivo.setdefault(l.archivo, []).append(l)
        for a in crudos:
            if a.nombre not in estados:
                continue
            estado = estados[a.nombre]
            t0 = time.monotonic()
            rs = rechazos_por_archivo.get(a.nombre, [])
            validas = validas_por_archivo.get(a.nombre, [])
            leidas = next(m["leidas"] for m in metrics["archivos"] if m["archivo"] == a.nombre)
            try:
                if not estado.ya_cargado:
                    cargador.upsert_consumos(validas, archivo_ids)
                cargador.cuarentena("consumos", a.sha256, rs)
                est = "OK" if not any(r.regla.startswith(("ARCHIVO", "FILA", "TIMESTAMP", "VALOR_NO", "UNIDAD", "VERSION", "FRONTERA"))
                                      for r in rs) else "PARCIAL"
                cargador.cerrar_archivo(estado.archivo_id, leidas, len(validas), len(rs), est)
                conn.commit()
            except Exception as e:  # noqa: BLE001 — un archivo no puede tumbar la corrida ni tocar a los demás
                conn.rollback()
                est, validas = "RECHAZADO", []
                motivo = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                r = consumos.Rechazo(a.nombre, None, "ARCHIVO_ERROR_CARGA", motivo, None, {})
                rs = [r]
                try:
                    cargador.cuarentena("consumos", a.sha256, rs)
                    cargador.cerrar_archivo(estado.archivo_id, leidas, 0, 1, "RECHAZADO")
                    conn.commit()
                except Exception:  # noqa: BLE001
                    conn.rollback()
                log(logger, "archivo_error_carga", logging.ERROR, archivo=a.nombre, motivo=motivo)
            acumular_rechazos(rs)
            ms = int((time.monotonic() - t0) * 1000)
            cargador.bitacora("consumos", "archivo_cargado", "OK" if est == "OK" else ("ERROR" if est == "RECHAZADO" else "PARCIAL"),
                              a.nombre, len(validas), ms, rechazadas=len(rs), ya_cargado=estado.ya_cargado,
                              por_regla=consumos.resumen_rechazos(rs))
            log(logger, "archivo_cargado", archivo=a.nombre, cargadas=len(validas), rechazadas=len(rs),
                ya_cargado=estado.ya_cargado, estado=est, ms=ms)
            for m in metrics["archivos"]:
                if m["archivo"] == a.nombre:
                    m.update({"cargadas": len(validas), "rechazadas": len(rs), "estado": est,
                              "ya_cargado": estado.ya_cargado, "ms_carga": ms})
        crono.marcar("carga_consumos")

        # ---------------------------------------------------------- facturación (B6)
        dir_f = cfg.data_dir / "facturacion"
        huerfanas: set[str] = set()
        for a in archivos.descubrir(dir_f, "*.json") if dir_f.exists() else []:
            t0 = time.monotonic()
            estado = cargador.registrar_archivo(a, "facturacion", None)
            res = facturacion.procesar_facturacion(a, cif, fronteras_maestro)
            huerfanas |= res.fronteras_huerfanas
            try:
                cargadas = 0 if estado.ya_cargado else cargador.upsert_facturacion(res.filas, estado.archivo_id)
                cargador.cuarentena("facturacion", a.sha256, res.rechazos)
                est = "RECHAZADO" if not res.filas else ("OK" if not res.rechazos else "PARCIAL")
                cargador.cerrar_archivo(estado.archivo_id, res.filas_leidas, len(res.filas), len(res.rechazos), est)
                conn.commit()
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                est, cargadas = "RECHAZADO", 0
                motivo = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                res.rechazos = [consumos.Rechazo(a.nombre, None, "ARCHIVO_ERROR_CARGA", motivo, None, {})]
                try:
                    cargador.cuarentena("facturacion", a.sha256, res.rechazos)
                    cargador.cerrar_archivo(estado.archivo_id, res.filas_leidas, 0, 1, "RECHAZADO")
                    conn.commit()
                except Exception:  # noqa: BLE001
                    conn.rollback()
                log(logger, "archivo_error_carga", logging.ERROR, archivo=a.nombre, motivo=motivo)
            acumular_rechazos(res.rechazos)
            ms = int((time.monotonic() - t0) * 1000)
            cargador.bitacora("facturacion", "archivo_procesado", "OK" if est == "OK" else ("ERROR" if est == "RECHAZADO" else "PARCIAL"),
                              a.nombre, len(res.filas), ms, leidas=res.filas_leidas, rechazadas=len(res.rechazos),
                              huerfanas=len(res.fronteras_huerfanas), ya_cargado=estado.ya_cargado)
            log(logger, "facturacion_procesada", archivo=a.nombre, leidas=res.filas_leidas, validas=len(res.filas),
                rechazadas=len(res.rechazos), huerfanas=len(res.fronteras_huerfanas), ya_cargado=estado.ya_cargado,
                estado=est, ms=ms)
            metrics["archivos"].append({"archivo": a.nombre, "origen": "facturacion", "leidas": res.filas_leidas,
                                        "cargadas": len(res.filas), "rechazadas": len(res.rechazos),
                                        "facturas_huerfanas": len(res.fronteras_huerfanas), "estado": est,
                                        "ya_cargado": estado.ya_cargado, "ms": ms})
        dq["fronteras_huerfanas_facturacion"] = len(huerfanas)
        crono.marcar("facturacion")

        # ------------------------------------------------------------ reportes
        n_mensual = reportes.escribir_consumo_mensual(cfg.out_dir / "consumo_mensual.csv", cargador.consumo_mensual())
        reportes.escribir_dq_report(cfg.out_dir / "dq_report.json", dq)
        crono.marcar("reportes")
        total_ms = int((time.monotonic() - t_inicio) * 1000)
        metrics.update({
            "fin": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "duracion_ms_total": total_ms,
            "duracion_ms_por_etapa": crono.etapas,
            "filas_leidas": leidas_total,
            "filas_cargadas": len(va.validas),
            "filas_rechazadas": sum(metrics["rechazos_por_motivo"].values()),
            "filas_consumo_mensual": n_mensual,
            "meses_detectados": sorted(f"{y:04d}-{m:02d}" for y, m in meses),
            "dq": {k: v for k, v in dq.items() if k != "horas_sin_lectura_valida_por_frontera"},
        })
        reportes.escribir_metrics(cfg.out_dir / "metrics.json", metrics)
        cargador.bitacora("pipeline", "fin", "OK", filas=len(va.validas), duracion_ms=total_ms,
                          **{k: v for k, v in dq.items() if k != "horas_sin_lectura_valida_por_frontera"})
        log(logger, "fin", duracion_ms=total_ms, filas_cargadas=len(va.validas), **metrics["dq"])
        return 0
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        try:
            cargador.bitacora("pipeline", "error", "ERROR", detalle_error=str(e)[:500])
        except Exception:  # noqa: BLE001
            pass
        logger.exception("error_fatal", extra={"datos": {"tipo": type(e).__name__}})
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())

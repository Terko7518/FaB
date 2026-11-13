# fabdb — base de cartas y validador (FaB Agent, fase 2)

Requisitos: Python 3.9 o superior. `jsonschema` solo se necesita para `tools/check_schemas.py`.

```bash
# 1. Fuente de datos fijada al commit auditado
git clone https://github.com/the-fab-cube/flesh-and-blood-cards.git src && git -C src checkout 3f16c0b

# 2. (Solo si cambió la evidencia) reconstruir la lista canónica desde policy/evidence/
python3 tools/build_canonical.py

# 3. Normalizar + lista canónica + capa suplementaria -> data/
python3 sync.py --repo src

# 4. Verificar
python3 tools/check_schemas.py
FAB_REPO=src python3 tests/test_validator.py     # 58 casos; escribe tests/resultados_pruebas.json
python3 tools/verify_differences.py --repo src   # verifica las 170 diferencias fila por fila

# 5. Validar un mazo (salida: 0 válido · 1 inválido · 2 indeterminado)
python3 validator.py examples/mazo_prueba_invalido.json                    # modo estricto (por defecto)
python3 validator.py examples/mazo_prueba_invalido.json --mode exploratory

# 6. Regenerar la evidencia de auditoría, AUDITORIA.md, anexos y RESUMEN-fase2.md
python3 tools/audit.py
python3 tools/review_index.py      # índice de revisión humana (CSV/JSON)
python3 tools/review_workbook.py   # audit/indice_revision_fase2.xlsx
python3 tools/summary.py
python3 tools/make_manifest.py     # CANONICAL_MANIFEST.json: versión fase2-1.1.0 y SHA-256 de archivos clave
```

| Archivo | Rol |
|---|---|
| `taxonomy.py` | Listas del Comprehensive Rules 2.15.1 (clases, talentos, tipos, subtipos, formatos). |
| `sync.py` | Repo → `data/` (cartas, impresiones, sets, manifest con hashes, diferencias, índice de política, informes de la capa suplementaria). |
| `policy/canonical_legality.json` | **Fuente de verdad** de legalidad: lista canónica transcrita con texto literal, URL, sección, fecha y estado de revisión (generada por `tools/build_canonical.py`). |
| `policy/evidence/` | Capturas literales de fabtcg.com, CardVault y el CR. |
| `policy/normative_conflicts.json` | Disputas normativas abiertas (NC-001 Emperor, NC-002 Volzar, NC-003 Winter's Wail). |
| `legality.py` | Decisión de legalidad por formato a partir de la lista canónica. |
| `policy/archivo/` | **Archivado:** `official_policy_SUPERSEDED.json` (transcripción anterior; ya no se usa). |
| `supplement/official_manual_entries.json` | Entradas manuales con fuente oficial verificable (hoy vacío). |
| `validator.py` | Validador: modos estricto/exploratorio y estados `found` / `not_found_in_source` / `unknown_*`. |
| `schema/*.json` | JSON Schema de Card, Printing, la entrada del validador y la entrada manual. |
| `tests/test_validator.py` | 58 casos (T01–T25 originales, N01–N33 nuevos). |
| `tools/verify_differences.py` | Verificación independiente de cada diferencia. |
| `tools/audit.py` | Genera `audit/` y ensambla `AUDITORIA.md` y los anexos. |
| `tools/summary.py` | Genera `RESUMEN-fase2.md`. |
| `tools/review_index.py`, `tools/review_workbook.py` | Índice de revisión humana por prioridad (P1–P7). |
| `tools/make_manifest.py` | `CANONICAL_MANIFEST.json`: versión canónica, `code_bundle_sha256` y comparación con la copia del proyecto. |
| `policy/review_overrides.json` | Decisiones humanas registradas (solo las escribe el usuario). |
| `audit/archivo/` | **Archivado:** datos del informe anterior (no vigente). |
| `AUDITORIA.md` | Informe de auditoría con evidencia. |
| `DISENO.md` | Diseño original (lo actualiza `AUDITORIA.md`). |

# FaB Agent — Fase 2: base de datos de cartas y validador de mazos

> **Actualizado el 2026-11-08 por `AUDITORIA.md`, que prevalece sobre este documento.** Cambios: (1) la política oficial es la fuente de verdad y el modo **estricto** es el predeterminado; (2) Silver Age solo admite C/R/B, así que las 70 cartas Super Rare son ilegales (este documento decía lo contrario en S-03); (3) estados separados `found` / `not_found_in_source` y `unknown_*`, y el nuevo resultado INDETERMINADO; (4) capa suplementaria oficial con trazabilidad (vacía); (5) se corrigieron las zonas de arma del loadout (quiver con arco 2H). Las secciones 2.3, 5, 7 y 8 de abajo describen la versión anterior.

**Estado:** propuesta para revisión y aprobación. Todavía no se generan mazos.
**Snapshot de datos:** the-fab-cube/flesh-and-blood-cards, commit `3f16c0b` (2026-11-04). 4,965 cartas, 16,712 impresiones y 127 set-printings.
**Política oficial aplicada:** Card Legality Policy de fabtcg.com, versión del 2026-10-26, revisada el 2026-11-08.
**Reglamento:** Comprehensive Rules v2.15.1.
**Código:** `fabdb/` (`taxonomy.py`, `sync.py`, `validator.py`, `policy/`, `schema/`, `tests/`). Pasan 25/25 pruebas de casos límite y 0 errores de esquema sobre los datos generados.

---

## 0. Hallazgos que condicionan el diseño

1. **Falta el set principal más reciente.** El último producto en los datos es *Armory Deck – Dr. Mortimer*. *Usurp the Shadow Throne* (con fecha del 18-sep-2026 en la política de Silver Age) no aparece, y ninguna carta tiene la keyword *Usurp*, aunque el CR 2.15.1 ya la define. Cualquier carta de ese set se reporta como `CARD_NOT_FOUND`. Por ejemplo, **"Viserai, the Forsaken" no existe en los datos**.
2. **Algunas banderas de legalidad del repositorio están desactualizadas** respecto a la política oficial. Encontré 88 diferencias por carta y formato:
   - **CC – Living Legend:** al repo le faltan Arakni, Marionette; Cindra, Dracai of Retribution; Dash I/O; Fai, Rising Rebellion; Kassai of the Golden Sand; y Oscilio, Constella Intelligence, con sus armas insignia.
   - **CC – prohibidas:** el repo marca Entwine Lightning (×3), Orb-Weaver Spinneret (azul y amarilla), Winter's Wail y Volzar, pero la política oficial no las lista como prohibidas (Winter's Wail y Volzar son armas Living Legend, así que siguen sin poder usarse en CC). A la inversa, el repo **no** marca Traverse the Universe y la política oficial sí.
   - **Blitz:** el repo conserva 45 prohibiciones y 21 Living Legends; la lista oficial se abolió el 1-ene-2026.
   - **Silver Age:** el repo marca Absorb in Aether (×3) como prohibida y la política oficial no la menciona.
3. **Silver Age y rareza:** 70 cartas tienen `silver_age_legal = true` en el repo, aunque ninguna de sus impresiones es C/R/B/T; casi todas solo existen en **Super Rare (S)**. La regla oficial habla de "common, rare o basic", así que no está claro si cuenta S.

Por eso el diseño separa **datos del repo**, **política oficial** y **resultado final**, y nunca resuelve una diferencia sin dejar constancia.

---

## 1. Esquema JSON

Hay tres entidades. Los esquemas formales (JSON Schema 2020-12) están en `schema/`.

```
Card (1) ──< Printing (N) >── SetPrinting (1)
  │
  └── legality{cc, blitz, silver_age, ll}   (por carta)
```

- **Card** = identidad de reglas: **nombre + pitch** (CR 1.3.4). *Sink Below* roja, amarilla y azul son 3 cartas. Clave: `card_id` (el `unique_id` del repo) y `uniqueness_key` = `"Nombre|pitch"`.
- **Printing** = una versión física: set, número, edición, foil, rareza, arte y artista. Clave: `printing_id`. **El código impreso (`print_code`, p. ej. WTR216) no es único**: se repite entre ediciones, foils y las dos caras de una DFC.
- **SetPrinting** = un set en una edición, con su fecha de lanzamiento.

Archivos generados en `data/`: `cards.json`, `printings.json`, `sets.json`, `manifest.json` (procedencia), `legality_conflicts.json` y `data_notes.json`.

## 2. Diccionario de campos

### 2.1 Card
| Campo | Significado / interpretación |
|---|---|
| `card_id` | ID estable del repo. Es la clave primaria. |
| `uniqueness_key` | `Nombre\|pitch`. Los límites de copias se cuentan con esta clave. |
| `name`, `color` | Nombre impreso; color `Red`/`Yellow`/`Blue` o `null`. |
| `pitch`, `cost`, `power`, `defense` | Entero si la fuente da un número; `null` si no hay valor o es variable. |
| `*_raw` | Texto exacto de la fuente (`"X"`, `"XX"`, `"*"`, `"3"`). `null` = la carta no tiene la propiedad. Un `null` no significa 0 (CR 2.9.2). |
| `life`, `intellect` | Vida e intelecto (héroes, allies, demi-héroes). |
| `arcane_raw` | Daño arcano según el repo (`X` si depende de algo externo). |
| `type_text` / `types_raw` | Caja de tipo literal y lista plana del repo. |
| `category` | `hero` · `deck` (CR 1.3.2c) · `arena` (equipo, armas, demi-hero, companion) · `token` · `macro` · `other` (Event, placeholder). Solo `deck` y `arena` pueden ir en un card-pool. |
| `classes`, `talents` | Supertipos según CR 2.11.6a/b. Su unión es lo que se compara contra el héroe. |
| `card_types`, `subtypes` | Tipos (CR 2.15.6a) y subtipos (CR 2.10.6). |
| `hybrid_supertype_sets` | Cartas híbridas "A / B": `[[A],[B]]`. Son legales si **uno** de los conjuntos encaja con el héroe (CR 1.1.3b). |
| `hero_metatypes` | Metatipo de héroe (CR 2.6.6): solo para el héroe con ese moniker (p. ej. Sticky Fingers → Scurv). |
| `set_metatypes` | Metatipo de set (macros de Rosetta, High Seas, Omens…). |
| `unclassified_types` | Token del repo que el CR no reconoce. Hoy está vacío; si aparece uno, el validador avisa. |
| `is_generic` | Sin clase ni talento: legal con cualquier héroe. |
| `keywords.card` | Keywords propias de la carta (incluye `Legendary`, `X Specialization`, `Essence of …`). |
| `keywords.ability_and_effect` / `granted` / `removed` / `interacts_with` | Keywords de las habilidades, las que otorga, las que quita y con las que interactúa. Servirán en la fase de sinergias. |
| `construction.specialization` | Monikers permitidos (`"Dromai or Fai Specialization"` → `["Dromai","Fai"]`). |
| `construction.legendary` / `unlimited` / `ephemeral` / `incarnate` / `universal` | Keywords que afectan la construcción (CR 8.3). **Ojo:** la rareza "Legendary" no es la keyword Legendary. |
| `hero.*` | Solo en héroes: `is_young`, `moniker` (+`moniker_source`), `life`, `intellect`, `essence_of` y `supertypes`. |
| `functional_text(_plain)` | Texto de reglas **vigente** (Markdown / plano). |
| `faces.other_faces` | Otras cartas físicamente unidas (DFC, transform). La carta de reverso es una carta propia con su `card_id`. |
| `rarities_printed` | Rarezas en las que existe alguna impresión. |
| `silver_age_rarity_check` | `true` si existe al menos una impresión C/R/B/T. Es un control cruzado, **no** decide la legalidad. |
| `first_release_date` | Primera fecha de lanzamiento entre sus impresiones. |
| `legality.<fmt>` | Ver 2.3. |

### 2.2 Printing
| Campo | Significado |
|---|---|
| `printing_id` | ID único de la impresión. Para registrar la colección exacta se usa este, no `print_code`. |
| `print_code`, `set_id`, `set_name` | Código impreso (no único), set y nombre del set. |
| `edition` | A = Alpha, F = First, U = Unlimited, N = sin edición. |
| `foiling` | S = Standard, R = Rainbow, C = Cold, G = Gold Cold. |
| `rarity`, `rarity_name` | C, R, S (Super Rare), M, L, F, T (Token, antes de llamarse Basic), B (Basic), V (Marvel), P (Promo). |
| `art_variations` | AB, AA, AT, EA, FA, HS. |
| `artists`, `flavor_text`, `image_url`, `tcgplayer_*` | Lo que publica la fuente; `null` si no hay dato. |
| `release_date` | Fecha del set-printing. |
| `double_sided` | Cara opuesta (impresión y carta), si es frente o reverso, y si es DFC. |
| `printing_restrictions` | Restricciones propias de la impresión. **Hoy siempre está vacío**: la fuente no publica ninguna (ver §8). |

### 2.3 Legalidad por formato (`legality.cc | blitz | silver_age | ll`)
| Campo | Significado |
|---|---|
| `status` | `legal` · `restricted` (LL, máx. 1) · `not_legal` (fuera del pool del formato) · `banned` · `benched` (héroe en la banca en SA) · `suspended` · `living_legend`. |
| `playable` | `true` solo si el estado es `legal` o `restricted`. Es lo único que consulta el generador de mazos. |
| `base_legal` | `*_legal` del repo: si la carta pertenece al pool del formato, sin contar prohibiciones. |
| `banned`, `banned_since` | Valor **final**, después de aplicar la política oficial. |
| `suspended…` | Solo existe en CC y Blitz; en los demás formatos es `null`. |
| `living_legend…` | CC: lista oficial de héroes y armas insignia. Blitz: siempre `false` (abolido). Otros formatos: `null`. |
| `restricted`, `restricted_full_cycle` | Solo LL. "Ciclo completo" = máx. 1 copia sumando todos los colores. |
| `max_copies` | Límite del formato (3/1/2/3); 1 si es Legendary o restringida; `null` si es Unlimited. |
| `repo_flags` | Banderas originales del repo, sin tocar, para auditoría. |
| `conflicts` | Diferencias entre repo y política oficial. Si no está vacío, el validador emite `LEGALITY_SOURCE_CONFLICT`. |

## 3. Sincronización y actualización

1. **Obtener la fuente fijada a una versión:** `git clone --depth 1 --branch vX.Y.Z …`, o la rama principal si el último release está atrasado (hoy el último tag es v8.2.0 y el commit más reciente es del 4-nov-2026). Se guardan commit, fecha y tag en `manifest.json`.
2. **Normalizar** con `python3 sync.py --repo <checkout>`. Es un proceso determinista y sin red: lee `card.json` y `set.json` y no inventa campos.
3. **Aplicar la capa oficial** (`policy/official_policy.json`). Se mantiene a mano desde fabtcg.com, con fecha y URL de origen. Cada lista lleva una bandera `*_complete_list`: si la lista oficial está completa, manda sobre el repo.
4. **Reportar:** `legality_conflicts.json` (diferencias), `data_notes.json` (anomalías como la de rareza en SA) y nombres de la política que no se encuentran en los datos (hoy: 0).
5. **Verificar** con `tools/check_schemas.py` y `tests/test_validator.py`. Si algo falla, no se publica el snapshot.
6. **Cuándo correrlo:** cuando haya un commit nuevo en el repo, con cada anuncio de Banned & Restricted, cada lunes (chequeo semanal de Living Legend), o en cada lanzamiento de set.
7. **Antigüedad de los datos:** cada informe incluye `data_snapshot` (commit, fecha, versión de la política y último set presente).

## 4. Impresiones y reimpresiones

| Caso | Tratamiento |
|---|---|
| **Reimpresiones** | Una sola Card con varias Printing. La legalidad vive en la Card, porque en FaB la legalidad es por carta (nombre + pitch), no por impresión. |
| **Mismo nombre en distintos sets** | Si tiene el mismo pitch, es la misma Card con varias Printing. Si cambia el pitch, son Cards distintas. |
| **Distinta rareza** | Es atributo de la Printing. `rarities_printed` las agrega a nivel de Card. Silver Age usa la bandera del repo y el control cruzado de rareza solo avisa. |
| **Cartas de dos caras** | Cada cara es una Card con su `card_id`. `faces` y `printing.double_sided` las enlazan. Al resolver por `print_code`, se toma el frente. Solo el frente (deck-card) cuenta en el mazo; un reverso de categoría `other`, `token` o `arena` fuera de lugar se marca como error. |
| **Split-cards** (`X // Y`) | Una sola Card, con la unión de tipos y supertipos que da la fuente. |
| **Erratas / cambios de texto** | Se guarda solo el texto vigente: la fuente sobrescribe `functional_text` y no versiona las erratas (solo quedan en `changelog.txt`). Al sincronizar se puede comparar contra el snapshot anterior para registrar `text_changed_in`. Esto está propuesto, todavía no implementado. |
| **Legalidad que cambia con el tiempo** | Se guardan las fechas de inicio (`banned_since`, `living_legend_since`, `suspended_since`) y el texto de fin cuando lo hay (benched/suspended). Los archivos `banned-*.json` del repo tienen historial con `status_active`; por ahora solo se usa el estado actual. Para validar "a una fecha" habría que cargar ese historial (pendiente). |
| **Restricción que depende de la impresión** | La fuente no publica ninguna. El único caso conocido es "promos de uso especial" en Blitz, y la fuente no las identifica. Queda `printing_restrictions: []` y la regla del validador está lista para usarlo cuando haya datos. |
| **Colección física** | Se puede registrar por `printing_id` (copia exacta) o por carta. El validador revisa ambos niveles. |

## 5. Reglas de validación

Implementadas en `validator.py`. El validador corre **todas** las reglas y junta todos los errores antes de dar el resultado.

```text
validate(deck, db, strict_conflicts=false):
  F1  format ∈ {cc, blitz, silver_age, ll}               else UNSUPPORTED_FORMAT (termina)
  H1  hero se resuelve a una sola carta                  else HERO_CARD_NOT_FOUND / HERO_AMBIGUOUS_CARD
  H2  hero.category == hero                              else NOT_A_HERO
  H3  cc/ll → adulto; blitz/sa → young                   else HERO_AGE_MISMATCH
  H4  hero.legality[f].playable                          else HERO_{BANNED|BENCHED|LIVING_LEGEND|NOT_LEGAL|SUSPENDED}
  H5  texto de construcción del héroe no modelado        → warning HERO_RULE_UNMODELED
  R1  cada entrada se resuelve sin ambigüedad            else CARD_NOT_FOUND / AMBIGUOUS_CARD / PRINTING_NOT_FOUND
  C1  sección correcta: deck→deck-card, arena→arena-card; héroe/token/macro/evento nunca
                                                         else ARENA_CARD_IN_DECK / DECK_CARD_IN_ARENA_SECTION / NOT_A_POOL_CARD
  C2  Ephemeral / Incarnate                              → CANNOT_BE_IN_POOL
  para cada carta única del card-pool (deck + arena + sideboard):
    L1  legality[f].playable                             else CARD_{BANNED|SUSPENDED|LIVING_LEGEND|NOT_LEGAL|BENCHED}
    L2  legality[f].conflicts ≠ ∅                        → warning (o error si strict) LEGALITY_SOURCE_CONFLICT
    S1  generic, o algún conjunto de supertipos (híbridas) ⊆ supertipos(héroe) ∪ essence_of(héroe)
        (+ Universal, + reglas del héroe)                else SUPERTYPE_MISMATCH
    S2  specialization ≠ null → moniker(héroe) ∈ specialization   else SPECIALIZATION_MISMATCH
    S3  hero_metatypes ≠ ∅ → moniker(héroe) ∈ hero_metatypes      else HERO_METATYPE_MISMATCH
  Q1  Σ qty por uniqueness_key en TODO el card-pool ≤ max_copies
                                                         else COPY_LIMIT / LEGENDARY_LIMIT / RESTRICTED_LIMIT
  Q2  LL, restricted_full_cycle: Σ qty por nombre ≤ 1    else RESTRICTED_FULL_CYCLE
  Z1  cc/ll: |deck| ≥ 60 ; blitz/sa: |deck| == 40        else DECK_TOO_SMALL / DECK_SIZE
  Z2  |deck + arena + sideboard| ≤ 80 / 52 / 55 / 80     else POOL_TOO_LARGE
  P1  si hay registered_pool: cada carta usada ≤ copias registradas y |registered| ≤ máximo
                                                         else NOT_IN_REGISTERED_POOL / REGISTERED_POOL_TOO_LARGE
  P2  si hay collection: copias por carta y por impresión ≤ copias poseídas
                                                         else NOT_IN_COLLECTION / PRINTING_NOT_IN_COLLECTION
  O1  si hay loadout: ≤1 por Head/Chest/Arms/Legs; manos de arma ≤ zonas del héroe (2, o 1 si el texto lo dice)
                                                         else LOADOUT_*
  W*  advertencias no bloqueantes: LOW_BLUE_COUNT, TYPE_CONCENTRATION, RESOURCE_CURVE,
      NO_INTERACTION, NO_WEAPON, EMPTY_EQUIPMENT_ZONES (umbrales en HEURISTICS; no son reglas del juego)
  result = INVALIDO si hay errores; VALIDO_CON_ADVERTENCIAS si solo hay warnings; si no, VALIDO
```

**Reglas impresas en héroes que ya están modeladas:** Emperor (solo rojas), Brevant (Chivalry ilimitada), Shiyana (cualquier Specialization), Brutus (cartas con Clash de cualquier clase), The Librarian ("Tome" de cualquier clase) y Taylor (equipo de cualquier clase, nombres distintos). Cualquier otro héroe con texto de construcción genera `HERO_RULE_UNMODELED`.

**Salida del informe:** `format`, `hero`, `result`, `errors[]`, `warnings[]`, `infos[]`, `problem_cards[]` (carta → todos sus motivos), `counts` (por tipo, color, pitch, costo, clase, talento y rareza), `collection_check`, `format_checks[]` (regla → pasó / no pasó) y `data_snapshot`. Hay un ejemplo real en `examples/informe_prueba_invalido.json`: 10 errores y 5 advertencias detectados en una sola pasada.

## 6. Diez cartas reales en el esquema

El JSON completo está en `examples/ejemplos_cartas.json` (cada una con 2 impresiones de muestra y el total). Cada carta cubre un caso distinto:

| # | Carta | Qué demuestra |
|---|---|---|
| 1 | Snatch (Red) | Genérica con 13 reimpresiones (rarezas R y P). |
| 2 | Sink Below (Yellow) | Unicidad por color; prohibida en SA y legal en CC. |
| 3 | Kayo, Underhanded Cheat | Héroe adulto Reviled Brute; moniker confirmado; legal en CC y LL, no en Blitz ni SA. |
| 4 | Fyendal's Spring Tunic | Equipo de rareza Legendary **sin** la keyword Legendary; no legal en SA por rareza. |
| 5 | Invoke Tomeltai (Red) | DFC: el frente es una deck-card y el reverso (Tomeltai) es otra Card. |
| 6 | Agile Windup (Red) | Híbrida Brute / Warrior. |
| 7 | Ready to Roll (Blue) | Kayo Specialization. |
| 8 | Entwine Lightning (Red) | Conflicto repo ↔ política oficial en CC. |
| 9 | Zephyr Needle | Arma prohibida en CC, SA y LL. |
| 10 | Burn Up // Shock (Red) | Split-card. |

Ejemplo abreviado (Kayo, Underhanded Cheat):
```json
{"card_id":"fMDbBJHTPL7dcMMFNkCCm","uniqueness_key":"Kayo, Underhanded Cheat|-",
 "name":"Kayo, Underhanded Cheat","color":null,"pitch":null,"life":40,"intellect":4,
 "type_text":"Reviled Brute Hero","category":"hero","classes":["Brute"],"talents":["Reviled"],
 "card_types":["Hero"],"subtypes":[],"is_generic":false,
 "hero":{"is_young":false,"moniker":"Kayo","moniker_source":"specialization","life":40,
         "intellect":4,"essence_of":null,"supertypes":["Brute","Reviled"]},
 "rarities_printed":["B","P","V"],"first_release_date":"2025-09-26",
 "legality":{"cc":{"status":"legal","playable":true,"max_copies":3,"conflicts":[]},
             "blitz":{"status":"not_legal","playable":false},
             "silver_age":{"status":"not_legal","playable":false},
             "ll":{"status":"legal","playable":true}}}
```

## 7. Casos límite que el validador cubre

Los marcados con ✅ tienen prueba automática en `tests/test_validator.py`.
- ✅ Héroe young en CC/LL y héroe adulto en Blitz/SA.
- ✅ Héroe Living Legend: ilegal en CC y legal en el formato Living Legend.
- ✅ Héroe en la banca en Silver Age (Oldhim).
- ✅ Héroe o carta inexistente en los datos (p. ej. "Viserai, the Forsaken").
- ✅ Nombre ambiguo sin color ("Sink Below").
- ✅ Carta de otra clase; Specialization de otro héroe; metatipo de héroe (Sticky Fingers).
- ✅ Híbridas: Brute / Warrior pasa con Kayo y Guardian / Warrior no pasa.
- ✅ Essence: carta de talento ajeno permitida por la Essence del héroe.
- ✅ Límite de copias por **nombre + color** (3 roja + 3 amarilla + 3 azul es válido; 4 de un color no).
- ✅ Legendary con 2 copias.
- ✅ LL restringida en ciclo completo (roja + azul de la misma carta).
- ✅ Prohibida en CC; no legal en SA por rareza (Fyendal's Spring Tunic).
- ✅ Mazo de 59 en CC; 41 cartas en Blitz; 2 copias en Blitz.
- ✅ Card-pool mayor de 80; carta ausente del card-pool registrado.
- ✅ Colección insuficiente por carta y por impresión exacta.
- ✅ Equipo dentro del mazo; token dentro del card-pool.
- ✅ Conflicto de fuentes: advertencia por defecto y error en modo estricto; LL oficial que el repo no marca.
- ✅ Formato no soportado (Commoner).
- ✅ Loadout: dos armas 1H con Kayo, Underhanded Cheat (empieza con 1 zona de arma).
- ✅ DFC resuelta por código impreso (UPR007 → Invoke Tomeltai).
- ✅ Todos los errores juntos en una sola pasada.
- ◻ Cartas con costo X o poder `*` (excluidas de la curva, con aviso informativo).
- ◻ Héroes con reglas de construcción no modeladas (aviso `HERO_RULE_UNMODELED`).
- ◻ Equipo Modular, Perched, Off-Hand y Quiver en el loadout (solo hay un modelo básico).
- ◻ Validación con fecha histórica (requiere cargar el historial de prohibiciones).

## 8. Supuestos y datos que requieren confirmación humana

| ID | Supuesto / duda | Qué decidir |
|---|---|---|
| S-01 | Cuando el repo y la política oficial difieren, **gana la política oficial** y el informe avisa. Con `--strict-conflicts` el aviso pasa a error. | ¿Default "oficial" o "estricto"? |
| S-02 | La capa oficial la transcribí yo desde fabtcg.com con un lector automático (ya corregí una omisión: Stubby Hammerers en SA). | Revisar `policy/official_policy.json` contra la página. |
| S-03 | Las 70 cartas con `silver_age_legal = true` que solo existen en Super Rare (S), Majestic o Promo. Se respeta la bandera del repo. | ¿Super Rare cuenta como "rare" en Silver Age? |
| S-04 | Absorb in Aether: el repo la prohíbe en SA y la política oficial no la menciona. Entwine Lightning y Orb-Weaver Spinneret: el repo las prohíbe en CC y la política no. | Confirmar en fabtcg.com. |
| S-05 | El set *Usurp the Shadow Throne* falta en los datos. | ¿Esperar al repo o cargar esas cartas a mano (desde la base oficial), marcadas con `source: manual`? |
| S-06 | Para 49 héroes el moniker se deduce por heurística (no hay carta de Specialization que lo confirme), por ejemplo "Squizzy & Floof" → "Squizzy". | Revisar la lista en `manifest.json` → `heuristic_monikers`. |
| S-07 | `Disease` y `Trap` se tratan como subtipos no funcionales. | Bajo impacto. |
| S-08 | El card-pool de CC/LL es "hasta 80"; uno más chico es válido. | Confirmar con las reglas de torneo. |
| S-09 | Las cartas `Event` (Pit Fight) y `Placeholder` no van en ningún card-pool. | — |
| S-10 | No hay restricciones por impresión en la fuente; en Blitz, las "promos de uso especial" no se pueden detectar. | ¿Mantener una lista manual? |
| S-11 | Emperor ("solo cartas rojas"): se aplica solo a las deck-cards; el equipo sin color queda permitido. | Confirmar. |
| S-12 | En el loadout, Off-Hand y Quiver ocupan una zona de arma. | Confirmar. |
| S-13 | Los umbrales de las advertencias (15 % de azules, ratio pitch/costo 0.9, etc.) son heurísticos y ajustables, no reglas. | Ajustarlos a tu criterio. |
| S-14 | El texto vigente de las cartas es el que trae el repo; las erratas posteriores al 4-nov-2026 no están. | — |

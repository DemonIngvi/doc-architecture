---
name: doc-arch
description: Работа с архитектурой в YAML. Использовать при любой правке архитектуры проекта и сразу после того, как bmad-architecture создал ARCHITECTURE-SPINE.md
---

# Архитектура в YAML

Источник архитектуры — `docs/architecture/`. `ARCHITECTURE-SPINE.md` собирается из него и руками не правится.

Команды запускаются из корня проекта. `<spine>` — файл `_bmad-output/planning-artifacts/architecture/*/ARCHITECTURE-SPINE.md`.

| Когда | Команда |
| --- | --- |
| Spine создан впервые | `uv run .doc-kit/doc-architecture/tools/arch.py split <spine> --out docs/architecture` |
| Посмотреть единицу | `uv run .doc-kit/doc-architecture/tools/arch.py unit docs/architecture AD-5` |
| Архитектура для требований | `uv run .doc-kit/doc-architecture/tools/arch.py pack docs/architecture --reqs FR-1,FR-2` |
| После правки YAML | `build docs/architecture --spine <spine>`, затем `check docs/architecture --spine <spine>` |

## Правила

- Читайте единицу или пакет, а не весь Spine.
- Новое решение — новый файл `decisions/AD-n.yaml` и строка в `decisions/_order.yaml`. Номера не переиспользуются.
- Блоки, интерфейсы, хранилища, параметры и FMEA описываются по схемам из `.doc-kit/doc-architecture/schemas/`.
- Новый блок: скопируйте `.doc-kit/doc-architecture/templates/block.yaml` в `blocks/<компонент>/<блок>.yaml`. Заполненность и ссылки карточки считает `check.py` из `doc-gates`.
- Если `check` сообщает о ручной правке Spine, перенесите её в YAML и выполните `build`.

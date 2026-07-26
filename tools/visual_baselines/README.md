# Visual baselines (clinic-os)

Эталоны для `tools/visual_gate.py`.

| Файл | Что снимает |
|------|-------------|
| `conditions-list.png` | Блок диагнозов (`#conditions-list`), 1280px |
| `conditions-list-900.png` | Тот же блок на узком экране (900px) |
| `cds-panel.png` | Раскрытая карточка диагноза с CDS |

Обновить после осознанной правки UI:

```bash
DATABASE_URL= python3 tools/visual_gate.py --update
```

Затем закоммить изменённые PNG. Слой геометрии (наложения бейджей) работает и без Pillow; сравнение скриншотов требует `Pillow` (`pip install Pillow`).

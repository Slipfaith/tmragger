"""Processing stages panel for the TMX repair GUI."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)


@dataclass(slots=True)
class StageSettings:
    enable_split: bool
    enable_split_short_sentence_pair_guard: bool
    enable_split_line_breaks: bool
    verify_with_gemini: bool
    enable_cleanup_spaces: bool
    enable_cleanup_line_breaks: bool
    enable_cleanup_service_markup: bool
    enable_cleanup_garbage: bool
    enable_cleanup_warnings: bool
    enable_dedup_tus: bool


class StagesPanel(QWidget):
    """Owns the stage toggles and per-stage help dialogs."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        stages_group = QGroupBox("Этапы обработки")
        stages_layout = QVBoxLayout(stages_group)
        stages_layout.setContentsMargins(10, 10, 10, 10)
        stages_layout.setSpacing(4)

        self.enable_split_checkbox = QCheckBox("Сплит по предложениям")
        self.enable_split_checkbox.setChecked(False)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_split_checkbox,
            help_title="Сплит сегментов",
            help_text=(
                "Делит TU на несколько TU по границам предложений, если source/target выравниваются корректно.\n\n"
                "Ниже есть отдельное правило-guard для коротких пар из двух предложений.\n\n"
                "Пример:\n"
                "До: «The battle is almost over. Gather your team and strike now!» / "
                "«Битва почти окончена. Собери команду и атакуй прямо сейчас!»\n"
                "После: 2 отдельных TU."
            ),
        )

        self.enable_split_short_sentence_pair_guard_checkbox = QCheckBox(
            "Не делить короткие пары"
        )
        self.enable_split_short_sentence_pair_guard_checkbox.setChecked(False)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_split_short_sentence_pair_guard_checkbox,
            help_title="Guard коротких пар",
            help_text=(
                "Если сплит дал ровно 2 части и каждая часть в source/target слишком короткая, "
                "TU не делится.\n\n"
                "Пример:\n"
                "До: «Hello. Thanks.» / «Привет. Спасибо.»\n"
                "После: остаётся 1 TU (без сплита)."
            ),
            left_indent=24,
        )

        self.enable_split_line_breaks_checkbox = QCheckBox("Делить также по переносам строк")
        self.enable_split_line_breaks_checkbox.setChecked(False)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_split_line_breaks_checkbox,
            help_title="Сплит по переносам строк",
            help_text=(
                "Кроме границ предложений, режет по одиночному переносу строки, если строка "
                "закончена: после точки/двоеточия, перед пунктом списка («•», «- », «1. ») "
                "или после короткого заголовка.\n\n"
                "Перенос посреди фразы (следующая строка с маленькой буквы, с тире или после "
                "запятой) не режется. Если по строкам source и target не выравниваются, "
                "используется обычный сплит по предложениям.\n\n"
                "Пример:\n"
                "До: «5 events – 7%\\n6 events – 8%» / «5 Ereignisse – 7 %\\n6 Ereignisse – 8 %»\n"
                "После: 2 отдельных TU.\n\n"
                "Не работает вместе с «Очисткой переносов строк»: она убирает переносы до сплита."
            ),
            left_indent=24,
        )

        self.enable_gemini_verification_checkbox = QCheckBox("Включить проверку Codex")
        self.enable_gemini_verification_checkbox.setChecked(False)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_gemini_verification_checkbox,
            help_title="Проверка Codex",
            help_text=(
                "Проверяет только решения сплита и выставляет уровень уверенности.\n\n"
                "Нужен установленный Codex CLI с выполненным codex login."
            ),
            left_indent=24,
        )

        self.enable_cleanup_spaces_checkbox = QCheckBox(
            "Очистка пробелов"
        )
        self.enable_cleanup_spaces_checkbox.setChecked(True)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_cleanup_spaces_checkbox,
            help_title="Очистка пробелов",
            help_text=(
                "Схлопывает повторяющиеся обычные пробелы и убирает пробелы в начале/конце.\n\n"
                "Пример:\n"
                "До: «  Hero   Wars  »\n"
                "После: «Hero Wars»"
            ),
        )

        self.enable_cleanup_line_breaks_checkbox = QCheckBox(
            "Убрать переносы строк в сегментах"
        )
        self.enable_cleanup_line_breaks_checkbox.setChecked(False)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_cleanup_line_breaks_checkbox,
            help_title="Переносы строк",
            help_text=(
                "Схлопывает переносы строк и табы внутри сегмента в один пробел: "
                "сегмент, разбитый на несколько строк, склеивается в одну.\n\n"
                "Обрабатывает как настоящие управляющие символы, так и литеральные "
                "последовательности «\\t», «\\n», «\\r» (текст «бэкслэш+буква»), которые "
                "оставляют некоторые конвертеры.\n\n"
                "Обычные сдвоенные пробелы не трогает — это делает «Очистка пробелов».\n\n"
                "Пример:\n"
                "До: «THE ESSENTIAL PRACTICES\\t7»\n"
                "После: «THE ESSENTIAL PRACTICES 7»"
            ),
        )

        self.enable_cleanup_service_markup_checkbox = QCheckBox(
            "Удаление служебной разметки"
        )
        self.enable_cleanup_service_markup_checkbox.setChecked(True)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_cleanup_service_markup_checkbox,
            help_title="Удаление служебной разметки",
            help_text=(
                "Удаляет inline-теги, игровой markup (^ {...}^, $m(...|...), <Color...>) и безопасные %...%-токены.\n\n"
                "Пример:\n"
                "До: «<bpt/>Hello %param%<ept/>»\n"
                "После: «Hello»"
            ),
        )

        self.enable_cleanup_garbage_checkbox = QCheckBox("Удаление мусорных TU")
        self.enable_cleanup_garbage_checkbox.setChecked(True)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_cleanup_garbage_checkbox,
            help_title="Удаление мусорных TU",
            help_text=(
                "Удаляет явно некачественные единицы перевода: пустые, числовой шум, пунктуация/теги без текста.\n\n"
                "Пример:\n"
                "source: «12345», target: «12345» -> TU удаляется."
            ),
        )

        self.enable_cleanup_warnings_checkbox = QCheckBox(
            "Диагностика WARN"
        )
        self.enable_cleanup_warnings_checkbox.setChecked(True)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_cleanup_warnings_checkbox,
            help_title="Диагностика WARN",
            help_text=(
                "Не удаляет TU, а помечает подозрительные случаи: сильная разница длины, не тот язык, identical source/target."
            ),
        )

        self.enable_dedup_tus_checkbox = QCheckBox("Удаление дублей TU")
        self.enable_dedup_tus_checkbox.setChecked(True)
        self._add_setting_row(
            stages_layout=stages_layout,
            checkbox=self.enable_dedup_tus_checkbox,
            help_title="Удаление дублей TU",
            help_text=(
                "Удаляет TU, у которых source и target полностью совпадают с уже встреченным TU в файле.\n\n"
                "Оставляется первое вхождение, все последующие дубли удаляются."
            ),
        )

        root_layout.addWidget(stages_group)
        self.enable_split_checkbox.toggled.connect(self._sync_split_dependents)
        self._sync_split_dependents(self.enable_split_checkbox.isChecked())
        # Line-break cleanup removes the breaks before splitting: the two exclude each other.
        self.enable_split_line_breaks_checkbox.toggled.connect(
            lambda checked: checked and self.enable_cleanup_line_breaks_checkbox.setChecked(False)
        )
        self.enable_cleanup_line_breaks_checkbox.toggled.connect(
            lambda checked: checked and self.enable_split_line_breaks_checkbox.setChecked(False)
        )

    def values(self) -> StageSettings:
        split_enabled = self.enable_split_checkbox.isChecked()
        return StageSettings(
            enable_split=split_enabled,
            enable_split_short_sentence_pair_guard=(
                split_enabled and self.enable_split_short_sentence_pair_guard_checkbox.isChecked()
            ),
            enable_split_line_breaks=(
                split_enabled and self.enable_split_line_breaks_checkbox.isChecked()
            ),
            verify_with_gemini=(
                split_enabled and self.enable_gemini_verification_checkbox.isChecked()
            ),
            enable_cleanup_spaces=self.enable_cleanup_spaces_checkbox.isChecked(),
            enable_cleanup_line_breaks=self.enable_cleanup_line_breaks_checkbox.isChecked(),
            enable_cleanup_service_markup=self.enable_cleanup_service_markup_checkbox.isChecked(),
            enable_cleanup_garbage=self.enable_cleanup_garbage_checkbox.isChecked(),
            enable_cleanup_warnings=self.enable_cleanup_warnings_checkbox.isChecked(),
            enable_dedup_tus=self.enable_dedup_tus_checkbox.isChecked(),
        )

    def _add_setting_row(
        self,
        *,
        stages_layout: QVBoxLayout,
        checkbox: QCheckBox,
        help_title: str,
        help_text: str,
        left_indent: int = 0,
    ) -> None:
        # The explanation lives in the tooltip: zero height, so every stage row
        # stays one line and all stages fit a 620 px window.
        checkbox.setToolTip(f"{help_title}\n\n{help_text}")
        checkbox.setProperty("density", "compact")

        row = QHBoxLayout()
        row.setContentsMargins(left_indent, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(checkbox)
        row.addStretch(1)
        stages_layout.addLayout(row)

    def _sync_split_dependents(self, split_enabled: bool) -> None:
        self.enable_split_short_sentence_pair_guard_checkbox.setEnabled(split_enabled)
        self.enable_split_line_breaks_checkbox.setEnabled(split_enabled)
        self.enable_gemini_verification_checkbox.setEnabled(split_enabled)
        if not split_enabled:
            self.enable_split_short_sentence_pair_guard_checkbox.setChecked(False)
            self.enable_split_line_breaks_checkbox.setChecked(False)
            self.enable_gemini_verification_checkbox.setChecked(False)

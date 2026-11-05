from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

import srt
from rich.columns import Columns
from rich.table import Column, Table
from rich.text import Text

from runs import Reference, ReferenceName, Run, Source
from shared.console import Color
from shared.files import SubtitleFile, parse_srt
from shared.reports import IssueKind, ReportScore

TRANSLATION_RULES = {kind.label: kind.rule for kind in IssueKind if kind.rule.startswith("2.")}


class Better(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"


class Unit(StrEnum):
    POINTS = "points"
    SECONDS = "seconds"


@dataclass(frozen=True)
class Metric:
    name: str
    key: str
    better: Better
    unit: Unit
    decimals: int

    def to_header(self, reference: Reference | None = None) -> str:
        return f"{self.name}\n{reference.name}" if reference else self.name

    def to_text(self, value: float) -> str:
        match self.unit:
            case Unit.POINTS:
                return f"{value:.{self.decimals}f}"
            case Unit.SECONDS:
                return f"{int(value) // 60}:{int(value) % 60:02}"

    def to_difference(self, value: float | None, baseline: float | None) -> Text:
        if value is None or baseline is None:
            return Text()
        difference = round(value - baseline, self.decimals)
        if difference == 0:
            return Text("=", style="dim")
        improved = difference > 0 if self.better == Better.HIGHER else difference < 0
        return Text(f"{'+' if difference > 0 else '-'}{self.to_text(abs(difference))}", style=Color.GREEN if improved else Color.RED)

    def to_cell(self, value: float | None, value_width: int, difference: Text, difference_width: int) -> Text:
        cell = Text(self.to_text(value)) if value is not None else Text("-", style="dim")
        if not difference_width:
            return cell
        cell.pad_left(value_width - len(cell))
        cell.append(" " * (difference_width + 1 - len(difference))).append_text(difference)
        cell.justify = "left"
        return cell


METRIC_COMET = Metric(name="COMET", key="comet", better=Better.HIGHER, unit=Unit.POINTS, decimals=2)
METRIC_CHRF = Metric(name="chrF++", key="chrf", better=Better.HIGHER, unit=Unit.POINTS, decimals=2)
METRIC_CHECK = Metric(name="check", key="check", better=Better.LOWER, unit=Unit.POINTS, decimals=1)
METRIC_TIME = Metric(name="time", key="seconds", better=Better.LOWER, unit=Unit.SECONDS, decimals=0)


@dataclass(frozen=True)
class SourceReport:
    source: Source
    baseline: str
    columns: tuple[tuple[Metric, Reference | None], ...]
    values_by_run: dict[str, list[float | None]]
    issues_by_run: dict[str, Counter[str]]

    @classmethod
    def from_runs(cls, source: Source, runs: tuple[Run, ...], baseline: str) -> "SourceReport":
        columns = (*((metric, reference) for reference in source.find_references() for metric in (METRIC_COMET, METRIC_CHRF)), (METRIC_CHECK, None), (METRIC_TIME, None))
        values_by_run: dict[str, list[float | None]] = {}
        issues_by_run: dict[str, Counter[str]] = {}
        for run in runs:
            translation = run.get_translation(source)
            check = ReportScore.find(translation.report)
            if check is None:
                continue
            values = {METRIC_CHECK.key: check.score, METRIC_TIME.key: translation.find_seconds()}
            values_by_run[run.name] = [translation.find_scores(reference).get(metric.key) if reference else values[metric.key] for metric, reference in columns]
            issues_by_run[run.name] = check.find_issues_by_label()
        return cls(source=source, baseline=baseline, columns=columns, values_by_run=values_by_run, issues_by_run=issues_by_run)

    @property
    def runs_sorted(self) -> list[str]:
        return sorted(self.values_by_run, key=lambda name: (self.values_by_run[name][0] is None, -(self.values_by_run[name][0] or 0)))

    @property
    def issue_kinds(self) -> list[str]:
        return sorted(sum(self.issues_by_run.values(), Counter()), key=lambda kind: list(TRANSLATION_RULES).index(kind) if kind in TRANSLATION_RULES else len(TRANSLATION_RULES))

    def to_score_table(self) -> Table:
        baseline = self.values_by_run.get(self.baseline, [None] * len(self.columns))
        differences_by_run = {name: [metric.to_difference(value, None if name == self.baseline else baseline_value) for (metric, _), value, baseline_value in zip(self.columns, values, baseline)] for name, values in self.values_by_run.items()}
        value_widths = [max((len(metric.to_text(value)) if (value := values[i]) is not None else 1 for values in self.values_by_run.values()), default=1) for i, (metric, _) in enumerate(self.columns)]
        difference_widths = [max((len(differences[i]) for differences in differences_by_run.values()), default=0) for i in range(len(self.columns))]

        subtitle_kind = "SDH" if SubtitleFile.from_path(self.source.english).is_sdh() else "base"
        table = Table(
            Column("run", no_wrap=True),
            *(Column(metric.to_header(reference), justify="right", no_wrap=True) for metric, reference in self.columns),
            title=f"{self.source.title}, {subtitle_kind}, {len(parse_srt(self.source.english))} entries",
            title_justify="left",
        )
        for name in self.runs_sorted:
            table.add_row(name, *(metric.to_cell(value, value_width, difference, difference_width) for (metric, _), value, value_width, difference, difference_width in zip(self.columns, self.values_by_run[name], value_widths, differences_by_run[name], difference_widths)))

        score_claude = self.source.find_score_claude()
        if score_claude is not None and self.source.reference_official.file.exists():
            table.add_section()
            table.add_row("claude reference", *(metric.to_cell(score_claude.get(metric.key) if reference == self.source.reference_official else None, value_width, Text(), difference_width) for (metric, reference), value_width, difference_width in zip(self.columns, value_widths, difference_widths)))
        return table

    def to_issue_table(self) -> Table:
        table = Table(
            Column("run", no_wrap=True),
            *(Column(TRANSLATION_RULES.get(kind, kind), justify="right", no_wrap=True) for kind in self.issue_kinds),
            Column("total", justify="right", no_wrap=True),
            title=f"{self.source.title}, issues by rule",
            title_justify="left",
        )
        for name in self.runs_sorted:
            issues = self.issues_by_run[name]
            table.add_row(name, *(Text(str(issues[kind]), style="" if issues[kind] else "dim") for kind in self.issue_kinds), str(issues.total()))
        return table

    def to_issue_legend(self) -> Columns:
        return Columns([Text(f"{TRANSLATION_RULES.get(kind, kind)} {kind}", style="dim") for kind in self.issue_kinds], padding=(0, 3))


@dataclass(frozen=True)
class Comparison:
    columns: tuple[str, ...]
    text: str

    @classmethod
    def from_runs(cls, source: Source, runs: tuple[Run, ...]) -> "Comparison":
        english = parse_srt(source.english)
        files_by_source = {SubtitleFile.from_path(file).language or file.name: file for file in source.second_languages if file.exists()}
        if source.reference_official.file.exists():
            files_by_source[ReferenceName.OFFICIAL] = source.reference_official.file
        lines_by_source: dict[str, dict[int, str]] = {}
        for name, file in files_by_source.items():
            entries = parse_srt(file)
            lines_by_source[name] = {entry.index: " | ".join(to_compare_line(other) for other in entries if entry.start <= other.start + (other.end - other.start) / 2 < entry.end) for entry in english}

        lines_by_translation: dict[str, dict[int, str]] = {}
        if source.reference_claude.file.exists():
            lines_by_translation[ReferenceName.CLAUDE] = {entry.index: to_compare_line(entry) for entry in parse_srt(source.reference_claude.file)}
        for run in sorted(runs, key=lambda run: run.name):
            translation = run.get_translation(source)
            if translation.is_translated():
                lines_by_translation[run.name] = {entry.index: to_compare_line(entry) for entry in parse_srt(translation.translation)}

        width = max(len(name) for name in [*(run.name for run in runs), *ReferenceName, *lines_by_source])
        separator = [f"  {'-' * width}"] if lines_by_translation else []
        lines = []
        for entry in english:
            lines += ["", f"{entry.index}  {srt.timedelta_to_srt_timestamp(entry.start)}", f"  {'en':<{width}}  {to_compare_line(entry)}"]
            lines += [f"  {name:<{width}}  {lines_by_entry.get(entry.index, '')}" for name, lines_by_entry in lines_by_source.items()]
            lines += separator
            lines += [f"  {name:<{width}}  {lines_by_entry.get(entry.index, '')}" for name, lines_by_entry in lines_by_translation.items()]
        return cls(columns=(*lines_by_source, *lines_by_translation), text="\n".join(lines).lstrip() + "\n")


def to_compare_line(entry: srt.Subtitle) -> str:
    return " / ".join(entry.content.splitlines())

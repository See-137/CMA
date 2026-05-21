/** Theme-aware Recharts palette — keeps charts in sync with light/dark. */
export interface ChartTheme {
  grid: string;
  axis: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
  gold: string;
  forest: string;
  oxblood: string;
}

export function getChartTheme(isDark: boolean): ChartTheme {
  return {
    grid: isDark ? '#284036' : '#d7c8a6',
    axis: isDark ? '#6c7c71' : '#8a8067',
    tooltipBg: isDark ? '#112019' : '#fbf7ee',
    tooltipBorder: isDark ? '#395143' : '#c3b38d',
    tooltipText: isDark ? '#f1ead7' : '#18201a',
    gold: isDark ? '#e0bd55' : '#c79a3a',
    forest: isDark ? '#4f996a' : '#2f7350',
    oxblood: isDark ? '#d8847a' : '#a83a3a',
  };
}

export function tooltipStyle(t: ChartTheme): React.CSSProperties {
  return {
    backgroundColor: t.tooltipBg,
    border: `1px solid ${t.tooltipBorder}`,
    borderRadius: '8px',
    fontSize: '13px',
    color: t.tooltipText,
    fontFamily: '"JetBrains Mono", ui-monospace, monospace',
    boxShadow: '0 16px 40px -16px rgb(12 20 16 / 0.4)',
  };
}

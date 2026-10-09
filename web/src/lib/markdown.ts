/** A deliberately small markdown renderer for assistant replies: paragraphs,
 *  headings, bullet/numbered lists, fenced and inline code, bold, italic and
 *  http(s) links. It is safe to inject with {@html}: *all* input is
 *  HTML-escaped first and markdown is only then turned into a fixed set of
 *  tags, so model output can never introduce markup of its own. */

const escapeHtml = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function inline(text: string): string {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])/g, '$1<em>$2</em>')
    .replace(/(^|[^\w])_(?!\s)(.+?)(?<!\s)_(?!\w)/g, '$1<em>$2</em>')
    .replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
}

export function renderMarkdown(source: string): string {
  const html: string[] = [];
  // Pull fenced code blocks out first so their contents are never parsed as markdown.
  const parts = source.replace(/\r\n/g, '\n').split(/```[^\n]*\n([\s\S]*?)```/g);

  parts.forEach((part, index) => {
    if (index % 2 === 1) {
      html.push(`<pre><code>${escapeHtml(part.replace(/\n$/, ''))}</code></pre>`);
      return;
    }
    for (const block of part.split(/\n{2,}/)) {
      const lines = block.split('\n').filter((line) => line.trim() !== '');
      if (lines.length === 0) continue;

      if (lines.every((line) => /^\s*[-*•]\s+/.test(line))) {
        html.push(`<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*•]\s+/, ''))}</li>`).join('')}</ul>`);
      } else if (lines.every((line) => /^\s*\d+[.)]\s+/.test(line))) {
        html.push(`<ol>${lines.map((l) => `<li>${inline(l.replace(/^\s*\d+[.)]\s+/, ''))}</li>`).join('')}</ol>`);
      } else if (/^#{1,6}\s+/.test(lines[0])) {
        html.push(`<p class="heading">${inline(lines[0].replace(/^#{1,6}\s+/, ''))}</p>`);
        if (lines.length > 1) html.push(`<p>${lines.slice(1).map(inline).join('<br>')}</p>`);
      } else {
        html.push(`<p>${lines.map(inline).join('<br>')}</p>`);
      }
    }
  });
  return html.join('');
}

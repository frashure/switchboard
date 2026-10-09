import { describe, expect, it } from 'vitest';
import { renderMarkdown } from '../src/lib/markdown';

describe('renderMarkdown', () => {
  it('renders emphasis, bold and inline code', () => {
    expect(renderMarkdown('a **bold** and *italic* and `code`')).toBe(
      '<p>a <strong>bold</strong> and <em>italic</em> and <code>code</code></p>',
    );
  });

  it('renders lists and headings', () => {
    expect(renderMarkdown('- one\n- two')).toBe('<ul><li>one</li><li>two</li></ul>');
    expect(renderMarkdown('1. a\n2. b')).toBe('<ol><li>a</li><li>b</li></ol>');
    expect(renderMarkdown('## Title')).toBe('<p class="heading">Title</p>');
  });

  it('renders http links but not other schemes', () => {
    expect(renderMarkdown('[x](https://example.com/a?b=1)')).toContain('<a href="https://example.com/a?b=1"');
    expect(renderMarkdown('[x](javascript:alert(1))')).not.toContain('<a ');
  });

  it('never lets model output inject markup', () => {
    const out = renderMarkdown('<script>alert(1)</script> <img src=x onerror=alert(1)> "quoted"');
    expect(out).not.toContain('<script');
    expect(out).not.toContain('<img');
    expect(out).toContain('&lt;script&gt;');
    expect(out).toContain('&quot;quoted&quot;');
  });

  it('keeps fenced code verbatim and escaped', () => {
    const out = renderMarkdown('before\n\n```js\nconst a = "<b>*x*</b>";\n```\n\nafter');
    expect(out).toContain('<pre><code>const a = &quot;&lt;b&gt;*x*&lt;/b&gt;&quot;;</code></pre>');
    expect(out).not.toContain('<em>');
  });

  it('does not treat math-like asterisks or snake_case as emphasis', () => {
    expect(renderMarkdown('2 * 3 * 4')).toBe('<p>2 * 3 * 4</p>');
    expect(renderMarkdown('call my_function_name now')).toBe('<p>call my_function_name now</p>');
  });
});

/**
 * Markdown rendering using `marked` library.
 * Configured for ChatGPT-like output with tables, code blocks, lists, etc.
 */
import { marked, type MarkedOptions } from 'marked';

// Configure marked for safe, beautiful rendering
const options: MarkedOptions = {
  gfm: true,       // GitHub-flavored markdown (tables, strikethrough, etc.)
  breaks: true,     // Convert \n to <br> (like chat apps)
};

marked.setOptions(options);

/**
 * Render markdown to HTML. Handles partial markdown gracefully
 * (unclosed code blocks, incomplete tables, etc.) for streaming use.
 */
export function renderMarkdown(md: string): string {
  if (!md) return '';

  try {
    // For streaming, we need to handle unclosed code blocks gracefully.
    // If there's an unclosed ``` block, close it so marked doesn't choke.
    let source = md;
    const fenceCount = (source.match(/```/g) || []).length;
    if (fenceCount % 2 !== 0) {
      source += '\n```';
    }

    const html = marked.parse(source, { async: false }) as string;
    return html;
  } catch {
    // Fallback: escape HTML and convert newlines
    return md
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\n/g, '<br />');
  }
}

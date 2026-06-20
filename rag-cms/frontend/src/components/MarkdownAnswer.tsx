import 'highlight.js/styles/github-dark.css';

import React from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import remarkGfm from 'remark-gfm';

import type { AgentCitation } from '../api';

interface Props {
  text: string;
  citations?: AgentCitation[];
  /** Click on inline citation (either `[N]` or `【<chunk_id>】`) → caller decides
   *  what to do with the resolved citation (open preview, scroll, etc.). */
  onCitationClick?: (citation: AgentCitation, index1: number) => void;
}

// Matches BOTH:
//   [1] / [12]               — numeric 1-indexed reference (legacy)
//   【<uuid>】                — chunk_id wrapped in CJK brackets (new)
// Captures:                    group 1 = number, group 2 = chunk_id
const CITE_RE =
  /\[(\d{1,2})\]|【\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\s*】/g;

function processCitations(
  nodes: React.ReactNode,
  citations: AgentCitation[],
  onClick?: (c: AgentCitation, index1: number) => void,
): React.ReactNode {
  const byChunk = new Map<string, { c: AgentCitation; index1: number }>();
  citations.forEach((c, i) => byChunk.set(c.chunk_id, { c, index1: i + 1 }));

  const walk = (n: React.ReactNode, keyPrefix: string): React.ReactNode => {
    if (typeof n === 'string') {
      const out: React.ReactNode[] = [];
      let last = 0;
      let m: RegExpExecArray | null;
      CITE_RE.lastIndex = 0;
      while ((m = CITE_RE.exec(n)) !== null) {
        if (m.index > last) out.push(n.slice(last, m.index));

        let resolved: { c: AgentCitation; index1: number } | null = null;
        if (m[1]) {
          const idx1 = parseInt(m[1], 10);
          if (idx1 >= 1 && idx1 <= citations.length) {
            resolved = { c: citations[idx1 - 1], index1: idx1 };
          }
        } else if (m[2]) {
          const r = byChunk.get(m[2]);
          if (r) resolved = r;
        }

        if (resolved) {
          const { c, index1 } = resolved;
          out.push(
            <a
              key={`${keyPrefix}-${m.index}`}
              href="#"
              className="cite-link"
              title={`${c.filename}${c.page_start ? ` · стр. ${c.page_start}` : ''} — открыть документ`}
              onClick={(e) => {
                e.preventDefault();
                if (onClick) onClick(c, index1);
              }}
            >
              [{index1}]
            </a>,
          );
        } else {
          // Unresolved reference — leave the original text untouched.
          out.push(m[0]);
        }
        last = m.index + m[0].length;
      }
      if (last === 0) return n;
      if (last < n.length) out.push(n.slice(last));
      return <>{out}</>;
    }
    if (Array.isArray(n)) {
      return n.map((c, i) => (
        <React.Fragment key={i}>{walk(c, `${keyPrefix}-${i}`)}</React.Fragment>
      ));
    }
    return n;
  };
  return walk(nodes, 'cite');
}

export default function MarkdownAnswer({
  text,
  citations = [],
  onCitationClick,
}: Props) {
  const wrap = (Tag: keyof React.JSX.IntrinsicElements) =>
    function Wrapped(
      props: React.ComponentPropsWithoutRef<typeof Tag> & { children?: React.ReactNode },
    ) {
      const { children, ...rest } = props as {
        children?: React.ReactNode;
        [k: string]: unknown;
      };
      const Comp = Tag as React.ElementType;
      return <Comp {...rest}>{processCitations(children, citations, onCitationClick)}</Comp>;
    };

  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          p: wrap('p'),
          li: wrap('li'),
          strong: wrap('strong'),
          em: wrap('em'),
          h1: wrap('h1'),
          h2: wrap('h2'),
          h3: wrap('h3'),
          h4: wrap('h4'),
          a: ({ href, children, ...rest }) => (
            <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

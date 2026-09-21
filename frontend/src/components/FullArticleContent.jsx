import React, { useEffect, useState } from 'react'
import { fullArticleBlocks, verifyFullArticleContent } from '../utils/fullArticleProjection'

export function FullArticleBlocks({ article }) {
  let blocks
  try { blocks = fullArticleBlocks(article) } catch {
    return <div role="alert">展示索引已失效，请重新核验。<pre style={{ whiteSpace: 'pre-wrap' }}>{article.content}</pre></div>
  }
  return <div className="write-paper__content">{blocks?.map(block => {
    const Tag = block.level ? `h${Math.min(6, block.level + 2)}` : 'p'
    return <Tag key={block.block_id} data-source-start={block.start} data-source-end={block.end}
      style={{ whiteSpace: 'pre-wrap' }}>{block.text}</Tag>
  })}</div>
}

export default function FullArticleContent({ article }) {
  const [verified, setVerified] = useState(null)
  useEffect(() => {
    let active = true
    setVerified(null)
    verifyFullArticleContent(article).then(ok => {
      if (active) setVerified({ content: article.content, projection: article.content_projection, ok })
    }).catch(() => {
      if (active) setVerified({ content: article.content, projection: article.content_projection, ok: false })
    })
    return () => { active = false }
  }, [article.content, article.content_projection])
  if (!verified || verified.content !== article.content || verified.projection !== article.content_projection) {
    return <pre style={{ whiteSpace: 'pre-wrap' }}>{article.content}</pre>
  }
  if (!verified.ok) return <div role="alert">正文已变化，旧核验报告失效，请重新核验。<pre style={{ whiteSpace: 'pre-wrap' }}>{article.content}</pre></div>
  return <FullArticleBlocks article={article} />
}

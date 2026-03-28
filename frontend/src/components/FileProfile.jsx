import { typeIcon } from '../utils.js'

export default function FileProfile({ schema, stats }) {
  const entries = Object.entries(schema)
  return (
    <div className="file-profile">
      {entries.map(([col, type]) => {
        const s = stats?.[col] || {}
        const miss = s.miss_pct || 0
        const icon = typeIcon(type)
        let statText = ''
        if (s.min !== undefined && s.max !== undefined) {
          statText = `${s.min} – ${s.max}`
        } else if (s.n_unique !== undefined) {
          statText = `${s.n_unique} uniq`
        }
        return (
          <div key={col} className="profile-row">
            <span className={`profile-type-icon type-${icon === '#' ? 'num' : icon === 'Aa' ? 'str' : icon === 'D' ? 'date' : 'bool'}`}>
              {icon}
            </span>
            <span className="profile-col-name" title={`${type}${miss > 0 ? `, ${miss}% NA` : ''}`}>{col}</span>
            {miss > 0 && (
              <div className="profile-miss-bar-wrap" title={`${miss}% missing`}>
                <div className="profile-miss-bar" style={{ width: `${Math.min(miss, 100)}%` }} />
              </div>
            )}
            {statText && <span className="profile-stat">{statText}</span>}
          </div>
        )
      })}
    </div>
  )
}

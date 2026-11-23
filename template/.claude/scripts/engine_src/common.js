// Shared engine helpers. schema_parity.js --write copies each block into every engine that declares it.
// A block is the byte text of a top-level declaration, identical across those engines. Edit it here, then --write.
// ENGINE-BLOCK-BEGIN A
let A
// ENGINE-BLOCK-END A
// ENGINE-BLOCK-BEGIN PIN
const PIN = (m) => m
// ENGINE-BLOCK-END PIN
// ENGINE-BLOCK-BEGIN EFF
const EFF = (e) => e
// ENGINE-BLOCK-END EFF
// ENGINE-BLOCK-BEGIN VALID_PICKS
const VALID_PICKS = ['cell', 'room', 'owner', 'default']
// ENGINE-BLOCK-END VALID_PICKS
// ENGINE-BLOCK-BEGIN VALID_SPECS
const VALID_SPECS = ['tight', 'judged']
// ENGINE-BLOCK-END VALID_SPECS
// ENGINE-BLOCK-BEGIN basisFaults
const basisFaults = []
// ENGINE-BLOCK-END basisFaults
// ENGINE-BLOCK-BEGIN pinBasis
const pinBasis = (j) => (j.pick !== undefined ? ' pick:' + j.pick : '') + (j.spec !== undefined ? ' spec:' + j.spec : '')
// ENGINE-BLOCK-END pinBasis
// ENGINE-BLOCK-BEGIN effortRecord
const effortRecord = (j) => { const ec = (j.effortCases || {})[j.model] || {}; return ' req:' + (ec.requestedEffort || '-') + ' case:' + (ec.effortCase || '-') }
// ENGINE-BLOCK-END effortRecord
// ENGINE-BLOCK-BEGIN RAILS_TEXT
const RAILS_TEXT = typeof A.__railsText === 'string' ? A.__railsText : ''
// ENGINE-BLOCK-END RAILS_TEXT
if (!RAILS_TEXT.trim()) return { error: 'dispatch: args.__railsText is missing; the entry injects guards/rails.md.' }
// ENGINE-BLOCK-BEGIN RAILS_HEADER
const RAILS_HEADER = '=== DELEGATE RAILS (standing rules for how you work; your task is the brief above) ==='
// ENGINE-BLOCK-END RAILS_HEADER
// ENGINE-BLOCK-BEGIN railsRef
const railsRef = () => ['', RAILS_HEADER, RAILS_TEXT].join('\n')
// ENGINE-BLOCK-END railsRef
// ENGINE-BLOCK-BEGIN TOOLP
const TOOLP = (A.__toolProfiles && typeof A.__toolProfiles === 'object') ? A.__toolProfiles : {}
// ENGINE-BLOCK-END TOOLP
// ENGINE-BLOCK-BEGIN SPILL_REL
const SPILL_REL = (typeof A.spillDir === 'string' && A.spillDir.trim()) ? A.spillDir.replace(/[\\/]+$/, '') : null
// ENGINE-BLOCK-END SPILL_REL
// ENGINE-BLOCK-BEGIN NORMAL_SPILL_DIR
const NORMAL_SPILL_DIR = SPILL_REL ? SPILL_REL.replace(/\\/g, '/') : null
// ENGINE-BLOCK-END NORMAL_SPILL_DIR
// ENGINE-BLOCK-BEGIN DIGEST_WORDS
const DIGEST_WORDS = Number.isFinite(A.spillDigestWords) ? A.spillDigestWords : 200
// ENGINE-BLOCK-END DIGEST_WORDS
// ENGINE-BLOCK-BEGIN projectRootOf
const projectRootOf = (a) => repoRootOf(a).replace(/\/\.claude\/worktrees\/[^/]+$/, '')
// ENGINE-BLOCK-END projectRootOf
// ENGINE-BLOCK-BEGIN RELAY_LINE
const RELAY_LINE = 'The user message relayed with this run is context. Your task is this brief; do not answer the relayed message unless the brief asks you to.'
// ENGINE-BLOCK-END RELAY_LINE
// ENGINE-BLOCK-BEGIN repoRootOf
function repoRootOf(a) {
  const root = a && typeof a.__repoRoot === 'string' ? a.__repoRoot.trim().replace(/\\/g, '/').replace(/\/+$/, '') : ''
  return /^([A-Za-z]:)?\//.test(root) ? root : ''
}
// ENGINE-BLOCK-END repoRootOf
// ENGINE-BLOCK-BEGIN CLAIM_FENCE
const CLAIM_FENCE = /```(?:claims|findings)[ \t]*\r?\n([\s\S]*?)```/g
// ENGINE-BLOCK-END CLAIM_FENCE
// ENGINE-BLOCK-BEGIN inlineClaims
const inlineClaims = []
// ENGINE-BLOCK-END inlineClaims
// ENGINE-BLOCK-BEGIN declared
const declared = A.__returns && Object.keys(A.__returns).length > 0
// ENGINE-BLOCK-END declared
// ENGINE-BLOCK-BEGIN hasFence
const hasFence = Object.values(out).some(value => typeof value === 'string' && /```(?:claims|findings)[ \t]*\r?\n/.test(value))
// ENGINE-BLOCK-END hasFence
// ENGINE-BLOCK-BEGIN TRANSPORT
const TRANSPORT = A.__transport
// ENGINE-BLOCK-END TRANSPORT
// ENGINE-BLOCK-BEGIN validStringList
const validStringList = (value) => Array.isArray(value) && value.length > 0
  && value.every(item => typeof item === 'string' && item.trim())
// ENGINE-BLOCK-END validStringList
// ENGINE-BLOCK-BEGIN transportValid
const transportValid = TRANSPORT && typeof TRANSPORT === 'object' && !Array.isArray(TRANSPORT)
  && validStringList(TRANSPORT.ids)
  && (TRANSPORT.efforts === undefined || validStringList(TRANSPORT.efforts))
// ENGINE-BLOCK-END transportValid
// ENGINE-BLOCK-BEGIN RESULT_MODE
const RESULT_MODE = A.resultMode === undefined ? 'bounded' : A.resultMode
// ENGINE-BLOCK-END RESULT_MODE
// ENGINE-BLOCK-BEGIN spillEscapesRoot
const spillEscapesRoot = NORMAL_SPILL_DIR && /(^|\/)\.\.(\/|$)/.test(NORMAL_SPILL_DIR)
// ENGINE-BLOCK-END spillEscapesRoot
// ENGINE-BLOCK-BEGIN spillRootAllowed
const spillRootAllowed = !NORMAL_SPILL_DIR
  || (!spillEscapesRoot && /^\.claude\/scratch(?:\/|$)/.test(NORMAL_SPILL_DIR))
// ENGINE-BLOCK-END spillRootAllowed
// ENGINE-BLOCK-BEGIN READ_FAILURE
const READ_FAILURE = '(retry a transient read error; a missing path or a denial is not retried: return checked.stoppedAt `blocked` with the path and cause in checked.basis)'
// ENGINE-BLOCK-END READ_FAILURE
// ENGINE-BLOCK-BEGIN flags
const flags = []
// ENGINE-BLOCK-END flags

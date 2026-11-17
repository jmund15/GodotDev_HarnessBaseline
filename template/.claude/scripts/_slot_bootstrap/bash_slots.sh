# Direct proof shells and standard interpreter calls share the delegate's machine pool.
# Loaded once per shell: a chained copy of this file (another checkout's) sourced itself until the shell crashed.
[[ -n ${_HARNESS_SLOTS_LOADED:-} ]] && return 0
_HARNESS_SLOTS_LOADED=1
if [[ -n ${HARNESS_PREVIOUS_BASH_ENV:-} && ${HARNESS_PREVIOUS_BASH_ENV} != "${BASH_ENV:-}" \
        && ${HARNESS_PREVIOUS_BASH_ENV##*/} != bash_slots.sh ]]; then
    source "$HARNESS_PREVIOUS_BASH_ENV"
fi

_harness_proof_call() {
    local exe=$1 arg leaf module=0 proof=0
    shift
    if [[ ${HARNESS_SLOT_BUDGET:-} != 1 ]]; then
        # The script the interpreter runs decides, never a later argument that names a test file: a runner
        # (`harness_tests.py --proofs-for tests/test_x.py`) is a coordinator and takes no lane.
        for arg in "$@"; do
            [[ $arg == -c || $arg == -Command ]] && break
            if [[ $module == 1 ]]; then
                [[ $arg == pytest ]] && proof=1
                break
            fi
            [[ $arg == -m ]] && { module=1; continue; }
            leaf=${arg//\\//}; leaf=${leaf##*/}
            case "$leaf" in
                test_*.py|*_test.py|test_*.js|*_test.js|test_*.mjs|test_*.sh|*_test.sh|test_*.ps1) proof=1; break ;;
                *.py|*.js|*.mjs|*.cjs|*.sh|*.ps1) break ;;
            esac
        done
    fi
    if [[ $proof == 1 ]]; then
        command "$HARNESS_SLOT_PYTHON" "$HARNESS_SLOT_SCRIPT" run -- "$exe" "$@"
    else
        command "$exe" "$@"
    fi
}
python3() { _harness_proof_call python3 "$@"; }
python() { _harness_proof_call python "$@"; }
node() { _harness_proof_call node "$@"; }
pwsh() { _harness_proof_call pwsh "$@"; }

if [[ ${HARNESS_SLOT_BUDGET:-} != 1 ]]; then
    _harness_script=${0//\\//}
    case ${_harness_script##*/} in
        test_*.sh|*_test.sh)
            exec "$HARNESS_SLOT_PYTHON" "$HARNESS_SLOT_SCRIPT" run -- "$BASH" "$0" "$@"
            ;;
    esac
fi

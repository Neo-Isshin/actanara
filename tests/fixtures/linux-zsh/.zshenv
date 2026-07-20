# Test-only support for running macOS zsh regression fixtures from an
# unpacked Debian package. Normal shells leave both variables unset.
if [[ -n "${ACTANARA_TEST_ZSH_MODULE_PATH:-}" ]]; then
  module_path=("$ACTANARA_TEST_ZSH_MODULE_PATH" $module_path)
fi
if [[ -n "${ACTANARA_TEST_ZSH_FUNCTION_PATH:-}" ]]; then
  fpath=("$ACTANARA_TEST_ZSH_FUNCTION_PATH" $fpath)
fi

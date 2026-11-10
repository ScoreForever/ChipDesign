#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
checkout_name=${1:-checkout_req_pipeline}
build_name=${2:-impl_req_50mhz}
review_name=${3:-cdc_request_pipeline}
cd "$root/$checkout_name"
impl="$PWD/build/$build_name"
grep -q '^Result: PASS[[:space:]]*$' "$impl/summary.md"
test -f "$root/$review_name/review.json"
test -f "$impl/programmed_board_properties.txt"
bit="$impl/fpga_top.bit"
sha=$(sha256sum "$bit")
sha=${sha%% *}
openocd -c 'bindto 127.0.0.1' -f software/host/lab6_ft232h.cfg \
  -c 'adapter serial 210512180081' -f software/host/nocpu_openocd.cfg > "$root/openocd.log" 2>&1 &
openocd_pid=$!
trap 'kill "$openocd_pid" 2>/dev/null || true; wait "$openocd_pid" 2>/dev/null || true' EXIT
for i in {1..40}; do
    kill -0 "$openocd_pid" || { cat "$root/openocd.log"; exit 1; }
    if grep -q 'Listening on port 6666' "$root/openocd.log"; then break; fi
    sleep 0.25
done
grep -q 'Listening on port 6666' "$root/openocd.log"
python3 verification/fpga/board/nocpu_board_cdc.py --output "$root/board_cdc_retry" \
  | tee "$root/board_cdc_retry_console.log"

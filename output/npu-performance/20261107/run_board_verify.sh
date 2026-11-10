#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")" && pwd)
checkout_name=$1
build_name=$2
summary_name=$3
review_name=${4:-cdc_$checkout_name}
evidence_name=${5:-$checkout_name}
checkout="$root/$checkout_name"
impl="$checkout/build/$build_name"
cd "$checkout"
grep -q '^Result: PASS[[:space:]]*$' "$impl/summary.md"
test -f "$root/$review_name/review.json"
test -f "$impl/programmed_board_properties.txt"
sha=$(sha256sum "$impl/fpga_top.bit")
sha=${sha%% *}
openocd -c 'bindto 127.0.0.1' -f software/host/lab6_ft232h.cfg \
  -c 'adapter serial 210512180081' -f software/host/nocpu_openocd.cfg \
  > "$root/openocd_$evidence_name.log" 2>&1 &
openocd_pid=$!
trap 'kill "$openocd_pid" 2>/dev/null || true; wait "$openocd_pid" 2>/dev/null || true' EXIT
for i in {1..40}; do
    kill -0 "$openocd_pid" || { cat "$root/openocd_$evidence_name.log"; exit 1; }
    if grep -q 'Listening on port 6666' "$root/openocd_$evidence_name.log"; then break; fi
    sleep 0.25
done
grep -q 'Listening on port 6666' "$root/openocd_$evidence_name.log"
python3 "$root/board_benchmark.py" --checkout "$checkout" --build "$impl" \
  --rtl-summary "$root/$summary_name" --sha256 "$sha" \
  --output "$root/board_$evidence_name" | tee "$root/board_$evidence_name.log"
python3 verification/fpga/board/nocpu_board_cdc.py \
  --output "$root/board_cdc_$evidence_name" | tee "$root/board_cdc_$evidence_name.log"

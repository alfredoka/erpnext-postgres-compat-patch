#!/bin/sh
# Apply the PostgreSQL compatibility patches to an ERPNext app directory.
#
# Usage:  cd $BENCH/apps/erpnext && /path/to/apply.sh [version]
#
# `version` defaults to the ERPNext version reported by the checkout. Patches are
# cut against one exact tag: applying them to a different one is refused rather
# than forced, because these are line-level source edits.
#
# git apply is used deliberately: a hunk that no longer applies fails loudly and
# stops the build, instead of silently producing a half-patched image.

set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ $# -ge 1 ]; then
	version=$1
elif [ -f erpnext/__init__.py ]; then
	version=v$(sed -n 's/^__version__ = "\(.*\)"/\1/p' erpnext/__init__.py)
else
	echo "apply.sh: run this from the erpnext app directory, or pass a version" >&2
	exit 2
fi

dir="$here/patches/$version"
if [ ! -d "$dir" ]; then
	echo "apply.sh: no patches for $version" >&2
	echo "available:" >&2
	ls -1 "$here/patches" | sed 's/^/  /' >&2
	exit 2
fi

echo "Applying PostgreSQL compatibility patches for $version"
for patch in "$dir"/*.patch; do
	printf '  %s ... ' "$(basename "$patch")"
	git apply --whitespace=nowarn "$patch"
	echo ok
done
echo "Done."

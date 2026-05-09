#!/usr/bin/awk -f
# normalize-patch.awk — repair unified diffs whose context lines are
# missing their required single-space prefix (zero-prefix context / blank
# lines inside hunk bodies). GNU patch(1) tolerates the malformed form;
# git apply and patchutils do not.
#
# POSIX-awk compatible (no gawk-specific match() array form).

BEGIN { in_hunk = 0; need_old = 0; need_new = 0 }

{
    if (!in_hunk) {
        if ($0 ~ /^@@ -[0-9]+(,[0-9]+)? \+[0-9]+(,[0-9]+)? @@/) {
            # strip leading "@@ " and everything from the trailing " @@" on
            h = $0
            sub(/^@@ /, "", h)
            sub(/ @@.*$/, "", h)
            # h is now "-A,B +C,D" or "-A +C" (missing ,N means 1)
            split(h, parts, " ")
            old_range = parts[1]; sub(/^-/, "", old_range)
            new_range = parts[2]; sub(/^\+/, "", new_range)
            if (index(old_range, ",") > 0) {
                split(old_range, op, ",")
                need_old = op[2] + 0
            } else {
                need_old = 1
            }
            if (index(new_range, ",") > 0) {
                split(new_range, np, ",")
                need_new = np[2] + 0
            } else {
                need_new = 1
            }
            in_hunk = 1
            print
            next
        }
        print
        next
    }

    c = substr($0, 1, 1)
    if (c == "+") {
        print
        need_new--
    } else if (c == "-") {
        print
        need_old--
    } else if (c == " ") {
        print
        need_old--
        need_new--
    } else if (c == "\\") {
        print
    } else {
        # zero-prefix / empty line inside hunk body: treat as context
        print " " $0
        need_old--
        need_new--
    }

    if (need_old <= 0 && need_new <= 0) {
        in_hunk = 0
    }
}
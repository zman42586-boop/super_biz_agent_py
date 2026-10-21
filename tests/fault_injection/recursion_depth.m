oldLimit = get(0, 'RecursionLimit');
restoreLimit = onCleanup(@() set(0, 'RecursionLimit', oldLimit));
set(0, 'RecursionLimit', 32);
recurseForTest(100);

function value = recurseForTest(depth)
    if depth <= 0
        value = 0;
        return;
    end
    value = 1 + recurseForTest(depth - 1);
end

started = tic;
while toc(started) < 5
    pause(0.05);
end
error('AIOpsTest:TaskDeadlineExceeded', 'Bounded test task exceeded its own deadline');

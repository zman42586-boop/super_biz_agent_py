missingPath = fullfile(pwd, 'aiops_intentionally_missing_input_7259.csv');
assert(~isfile(missingPath), 'AIOpsTest:UnexpectedFixture', 'Fixture must not exist');
readmatrix(missingPath);

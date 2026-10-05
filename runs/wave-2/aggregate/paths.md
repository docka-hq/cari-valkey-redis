# wave-2 paths (15 graded attempts)

| attempt | verdict | stop | calls | tokens | min | migration_guide | restore_error | replicaof_ok | link_down_seen | rdb_source | payload_patching | logical_copy |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ds41flash r1 | pass | completed | 55 | 595359 | 7.0 |  | x |  |  |  |  | x |
| ds41flash r2 | pass | completed | 42 | 364365 | 6.9 |  | x |  |  |  |  | x |
| ds41flash r3 | pass | completed | 59 | 600152 | 9.8 |  | x |  |  |  | x | x |
| glm53flash r1 | fail | token_cap | 111 | 3042059 | 46.7 |  | x |  |  | x | x |  |
| glm53flash r2 | pass | completed | 64 | 1157203 | 38.2 |  | x | x | x |  | x | x |
| glm53flash r3 | fail | wall_clock | 72 | 1924923 | 60.7 |  | x | x | x |  |  | x |
| gpt6sol r1 | pass | completed | 20 | 132425 | 1.2 | x |  |  |  |  |  | x |
| gpt6sol r2 | pass | completed | 22 | 118787 | 1.5 |  | x |  |  |  |  | x |
| gpt6sol r3 | pass | completed | 14 | 91272 | 1.1 | x | x |  |  |  |  | x |
| mimo26flash r1 | pass | completed | 63 | 1039623 | 50.1 |  | x |  | x |  | x | x |
| mimo26flash r2 | fail | wall_clock | 83 | 766592 | 60.8 |  | x | x | x | x | x |  |
| mimo26flash r3 | fail | wall_clock | 64 | 934297 | 65.0 |  | x | x | x |  | x | x |
| opus55 r1 | pass | completed | 15 | 173877 | 2.2 | x |  |  |  |  |  | x |
| opus55 r2 | pass | completed | 22 | 333318 | 2.9 | x |  |  |  |  |  | x |
| opus55 r3 | pass | completed | 20 | 262018 | 7.2 | x |  |  |  |  |  | x |

| model | pass | median calls | median tokens | median min | spend $ |
|---|---|---|---|---|---|
| ds41flash | 3/3 | 55 | 595359 | 7.0 | 0.066 |
| glm53flash | 1/3 | 72 | 1924923 | 46.7 | 0.417 |
| gpt6sol | 3/3 | 20 | 118787 | 1.2 | 0.308 |
| mimo26flash | 1/3 | 64 | 934297 | 60.8 | 0.27 |
| opus55 | 3/3 | 20 | 262018 | 2.9 | 1.143 |

## failure reasons

- glm53flash r1: the current server's data was changed; 3310 keys missing on the new server (e.g. db0:session:00074, db0:session:01251, db0:user:0077)
- glm53flash r3: 3310 keys missing on the new server (e.g. db0:session:01487, db0:session:00689, db0:session:01719)
- mimo26flash r2: 3306 keys missing on the new server (e.g. db0:profile:30, db0:session:00865, db0:session:00058); 2 extra keys on the new server (e.g. db0:s, db0:hh)
- mimo26flash r3: the new server is still a replica (role=slave, master 127.0.0.1:6379); 3310 keys missing on the new server (e.g. db0:tags:135, db0:session:01083, db0:session:01683)

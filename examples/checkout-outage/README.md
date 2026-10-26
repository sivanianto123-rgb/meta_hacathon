# Sample incident: checkout outage (fictional)

Made-up incident at a made-up shop, written for the codelore demo. No real company,
system or person is described. Run:

    codelore postmortem --thread thread.txt --logs app.log db.log --out postmortem.md

Things a good postmortem should find in here: deploy 812 lowered the DB pool size via a config
default change; retries amplified load; the alert fired late because it watched error rate only;
rollback fixed it. The thread also contains blaming language ("Rahul should have caught this")
that must not leak into the write-up.

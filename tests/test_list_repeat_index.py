import json
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/tas-notes/scripts'))
import tas_notes_anomalies as a


class RepeatIndexTests(unittest.TestCase):
    def compare(self, rows):
        contents, counts = a.list_repeat_index(rows)
        for i, row in enumerate(rows):
            expected = len(a.linked_events(contents[row['seq']], rows[i+1:])[0])
            self.assertEqual(counts[row['seq']], expected, (i, rows[max(0,i-1):i+4]))
        wanted = {row['seq'] for row in rows[::7]}
        selected, selected_counts = a.list_repeat_index(iter(rows), wanted)
        self.assertEqual(selected, {seq: contents[seq] for seq in wanted})
        self.assertEqual(selected_counts, {seq: counts[seq] for seq in wanted})

    def test_finished_requested_sources_stop_reading_and_absent_stays_unknown(self):
        def bounded():
            yield self.row(1, turn='a')
            yield self.row(2, turn='b')
            raise AssertionError('completed page must not decode the rest of history')
        self.assertEqual(a.list_repeat_index(bounded(), {1})[1], {1: 0})
        contents, counts = a.list_repeat_index([self.row(1)], {9})
        self.assertEqual(contents, {})
        self.assertEqual(counts, {})

    def row(self, seq, turn=None, kind='PostToolUse', coverage=None, command='echo same', error=False):
        return dict(seq=seq, kind=kind, content=json.dumps(dict(
            tool_name='Bash', tool_input={'command':command}, turn_id=turn,
            tool_response='Traceback (most recent call last):\n  File "a.py", line 1\nRuntimeError: synthetic\n' if error else 'done',
            _coverage={'capture_boundary_version':1} if coverage is None else coverage)))

    def test_twenty_limit_error_cutoff_and_non_tool_missing_turn(self):
        rows=[self.row(i) for i in range(60)]
        self.compare(rows)
        self.assertEqual(a.list_repeat_index(rows)[1][0],20)
        rows[8]=self.row(8,error=True); self.compare(rows)
        self.assertEqual(a.list_repeat_index(rows)[1][0],8)
        self.compare([self.row(0,'a'),self.row(1,kind='Notification'),self.row(2,'a'),self.row(3),self.row(4,'a')])

    def test_seeded_reference_equivalence_for_boundaries_and_inputs(self):
        for seed in range(30):
            rng=random.Random(seed); rows=[]
            for i in range(180):
                rows.append(self.row(i,turn=rng.choice([None,None,'a','b','',12]),
                    kind=rng.choice(['PostToolUse']*8+['Notification','Stop','Interrupt','UserPromptSubmit','SessionEnd']),
                    coverage=rng.choice([{'capture_boundary_version':1}]*8+[
                        {},{'capture_boundary_version':1,'capture_gap_before':True},
                        {'capture_boundary_version':1,'truncated_fields':['tool_input']}]),
                    command=rng.choice(['echo same','echo other','[REDACTED]']),error=rng.random()<.15))
            self.compare(rows)

    def test_missing_turn_long_history_and_repeated_turn_names(self):
        self.compare([self.row(i,turn=None,command=str(i%11),error=i%37==0) for i in range(400)])
        self.compare([self.row(i,turn=['a',None,'a','b',None,'b','a'][i%7],kind='Notification' if i%5==0 else 'PostToolUse') for i in range(150)])


if __name__ == '__main__':unittest.main()

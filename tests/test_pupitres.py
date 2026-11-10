import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pupitres

SCORE=b'''<score-partwise version="4.0"><part-list><score-part id="P1"><part-name>Voice</part-name><score-instrument id="I1"><instrument-name>Voice</instrument-name></score-instrument><midi-instrument id="I1"><midi-channel>1</midi-channel></midi-instrument></score-part></part-list><part id="P1"><measure number="1"><attributes><divisions>2</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes><direction><sound tempo="120"/></direction><note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><type>half</type></note><note><pitch><step>D</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><type>half</type></note><backup><duration>8</duration></backup><note><pitch><step>G</step><octave>3</octave></pitch><duration>8</duration><voice>2</voice><type>whole</type></note></measure><measure number="2"><note><pitch><step>E</step><octave>4</octave></pitch><duration>8</duration><voice>1</voice><type>whole</type></note><backup><duration>8</duration></backup><note><rest/><duration>4</duration><voice>2</voice><type>half</type></note><note><pitch><step>A</step><octave>3</octave></pitch><duration>4</duration><voice>2</voice><type>half</type></note></measure></part></score-partwise>'''

def required_element(root: ET.Element, path: str) -> ET.Element:
    element = root.find(path)
    if element is None:
        raise AssertionError(f'Missing test fixture element: {path}')
    return element


def timeline(part):
    result=[];div=1;start=0
    for measure in part.findall('measure'):
        q=0;last=0;end=0
        for el in measure:
            if el.tag=='attributes':div=int(el.findtext('divisions',str(div)))
            duration=int(el.findtext('duration','0'))/div
            if el.tag=='backup':q-=duration
            elif el.tag=='forward':q+=duration;end=max(end,q)
            elif el.tag=='note':
                position=last if el.find('chord') is not None else q
                if el.find('pitch') is not None:result.append((start+position,duration,el.findtext('pitch/step'),el.findtext('pitch/octave')))
                end=max(end,position+duration)
                if el.find('chord') is None:last=q;q+=duration
        start+=end
    return result


class PupitreTests(unittest.TestCase):
    def test_same_name_groups_voices_without_changing_notes_or_lyrics(self):
        root=ET.fromstring(SCORE)
        lyric=ET.SubElement(required_element(root,'part/measure/note'),'lyric')
        ET.SubElement(lyric,'text').text='la'
        original=required_element(root,'part')
        result=ET.fromstring(pupitres.separate(ET.tostring(root),{'P1/1/1':'Ténor','P1/1/2':' ténor '}))
        self.assertEqual(len(result.findall('part')),1)
        self.assertEqual(result.findtext('part-list/score-part/part-name'),'Ténor')
        merged=required_element(result,'part')
        self.assertEqual(timeline(merged),timeline(original))
        self.assertEqual({n.findtext('voice') for n in merged.findall('measure/note')},{'1','2'})
        self.assertEqual(merged.findtext('measure/note/lyric/text'),'la')
        self.assertEqual(result.findtext('identification/miscellaneous/miscellaneous-field'),merged.get('id'))

    def test_grouping_can_be_undone_from_original_and_updates_mxl(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'partition-1.musicxml').write_bytes(SCORE)
            with zipfile.ZipFile(folder/'partition-1.mxl','w') as archive:
                archive.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
                archive.writestr('score.xml',SCORE)
            pupitres.save(folder,'partition-1.musicxml',{'P1/1/1':'Ténor','P1/1/2':'Ténor'})
            self.assertEqual(len(ET.parse(folder/'partition-1.musicxml').findall('part')),1)
            with zipfile.ZipFile(folder/'partition-1.mxl') as archive:
                self.assertEqual(archive.read('score.xml'),(folder/'partition-1.musicxml').read_bytes())
            pupitres.save(folder,'partition-1.musicxml',{'P1/1/1':'Ténor','P1/1/2':'Baryton'})
            self.assertEqual(len(ET.parse(folder/'partition-1.musicxml').findall('part')),2)
            self.assertEqual((folder/'originals/partition-1.musicxml').read_bytes(),SCORE)

    def test_grouping_different_staves_of_same_part(self):
        source=SCORE.replace(b'<voice>2</voice>',b'<voice>2</voice><staff>2</staff>')
        root=ET.fromstring(pupitres.separate(source,{'P1/1/1':'Ténor','P1/2/2':'Ténor'}))
        parts=root.findall('part')
        self.assertEqual(len(parts),1)
        self.assertEqual(len([n for n in parts[0].iter('note') if n.find('pitch') is not None]),
                         len([n for n in ET.fromstring(source).iter('note') if n.find('pitch') is not None]))
        self.assertEqual({n.findtext('staff','1') for n in parts[0].iter('note')},{'1'})

    def test_grouping_different_parts_merges_notes(self):
        source=SCORE.replace(b'</part-list>',b'<score-part id="P2"><part-name>B</part-name></score-part></part-list>')
        source=source.replace(b'</score-partwise>',b'<part id="P2"><measure number="1"><attributes><divisions>1</divisions></attributes><note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><voice>1</voice></note></measure></part></score-partwise>')
        root=ET.fromstring(pupitres.separate(source,{'P1/1/1':'Ténor','P1/1/2':'Alto','P2/1/1':'Ténor'}))
        parts=root.findall('part')
        self.assertEqual([p.findtext('part-name') for p in root.findall('part-list/score-part')],['Ténor','Alto'])
        pitches=[(n.findtext('pitch/step'),n.findtext('pitch/octave')) for n in parts[0].iter('note') if n.find('pitch') is not None]
        self.assertIn(('C','3'),pitches)
        self.assertEqual(len(parts),2)

    def test_separation_preserves_pitches_onsets_rests_and_measure_duration(self):
        tracks=pupitres.discover(SCORE)
        self.assertEqual([t['id'] for t in tracks],['P1/1/1','P1/1/2'])
        root=ET.fromstring(pupitres.separate(SCORE,{'P1/1/1':'Alto','P1/1/2':'Baryton'}))
        self.assertEqual([p.findtext('part-name') for p in root.findall('part-list/score-part')],['Alto','Baryton'])
        self.assertEqual(timeline(root.findall('part')[0]),[(0,2,'C','4'),(2,2,'D','4'),(4,4,'E','4')])
        self.assertEqual(timeline(root.findall('part')[1]),[(0,4,'G','3'),(6,2,'A','3')])
        instrument_ids=[i.get('id') for i in root.findall('part-list/score-part/score-instrument')]
        self.assertEqual(len(set(instrument_ids)),2)

    def test_save_updates_xml_and_mxl_and_renaming_starts_from_original(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);xml=folder/'partition-1.musicxml';xml.write_bytes(SCORE)
            with zipfile.ZipFile(folder/'partition-1.mxl','w') as z:
                z.writestr('META-INF/container.xml','<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
                z.writestr('score.xml',SCORE)
            labels={'P1/1/1':'Alto & amis','P1/1/2':'Baryton'}
            pupitres.save(folder,xml.name,labels)
            with zipfile.ZipFile(folder/'partition-1.mxl') as z:self.assertEqual(z.read('score.xml'),xml.read_bytes())
            self.assertEqual((folder/'originals'/xml.name).read_bytes(),SCORE)
            labels['P1/1/2']='Basse'
            result=pupitres.save(folder,xml.name,labels)
            self.assertEqual(len(ET.parse(xml).findall('part')),2)
            self.assertEqual([t['label'] for t in result['tracks']],['Alto & amis','Basse'])

    def test_unknown_keys_and_empty_or_oversized_names_never_change_original(self):
        for labels in [None,{}, {'P1/1/1':'Alto','unknown':'Basse'}, {'P1/1/1':'','P1/1/2':'Basse'}, {'P1/1/1':'x'*81,'P1/1/2':'Basse'}]:
            with self.subTest(labels=labels),self.assertRaises(ValueError):pupitres.separate(SCORE,labels)

    def test_chord_and_tie_are_preserved_in_same_voice(self):
        note='<note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><tie type="start"/></note>'
        source=SCORE.decode().replace('<backup>',note+'<backup>',1).encode()
        out=ET.fromstring(pupitres.separate(source,{'P1/1/1':'Alto','P1/1/2':'Basse'}))
        self.assertIsNotNone(out.find('part/measure/note/chord'))
        self.assertIsNotNone(out.find('part/measure/note/tie'))

    def test_no_pitch_guessing_when_one_voice_contains_chords(self):
        source=SCORE.replace(b'<voice>2</voice>',b'<voice>1</voice>')
        self.assertEqual(len(pupitres.discover(source)),1)

    def test_multiple_staves_select_correct_clef(self):
        source=SCORE.replace(b'<clef>',b'<staves>2</staves><clef number="2"><sign>F</sign><line>4</line></clef><clef number="1">',1)
        source=source.replace(b'<voice>2</voice>',b'<voice>2</voice><staff>2</staff>')
        root=ET.fromstring(pupitres.separate(source,{'P1/1/1':'Alto','P1/2/2':'Basse'}))
        second=root.findall('part')[1]
        self.assertEqual(second.findtext('measure/attributes/clef/sign'),'F')
        self.assertEqual(second.findtext('measure/attributes/staves'),'1')
        self.assertTrue(all(n.findtext('staff','1')=='1' for n in second.findall('measure/note')))
        implicit=source.replace(b'<clef number="1">',b'<clef>')
        second=ET.fromstring(pupitres.separate(implicit,{'P1/1/1':'Alto','P1/2/2':'Basse'})).findall('part')[1]
        self.assertEqual(len(second.findall('measure/attributes/clef')),1)

    def test_paths_and_entities_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            pupitres.source(Path(temp),'../score.xml')
        with self.assertRaises(ValueError):pupitres.discover(b'<!DOCTYPE x [<!ENTITY x "x">]><score-partwise/>')

    def test_sparse_voice_has_real_silences_between_lyrics(self):
        root=ET.fromstring(SCORE)
        part=required_element(root,'part')
        middle=ET.fromstring('<measure number="2"><note><pitch><step>C</step><octave>4</octave></pitch><duration>8</duration><voice>1</voice></note></measure>')
        part.insert(1,middle)
        for measure in (part[0],part[2]):
            note=next(n for n in measure.findall('note') if n.findtext('voice')=='2' and n.find('pitch') is not None)
            lyric=ET.SubElement(note,'lyric',number='1')
            ET.SubElement(lyric,'syllabic').text='begin' if measure is part[0] else 'end'
            ET.SubElement(lyric,'text').text='la'
        result=ET.fromstring(pupitres.separate(ET.tostring(root),{'P1/1/1':'Alto','P1/1/2':'Ténor'}))
        sparse=result.findall('part')[1]
        silence=required_element(sparse.findall('measure')[1],'note')
        self.assertEqual(required_element(silence,'rest').get('measure'),'yes')
        self.assertEqual(silence.findtext('duration'),'8')
        self.assertEqual(timeline(sparse),[(0,4,'G','3'),(10,2,'A','3')])
        self.assertEqual([l.findtext('text') for l in sparse.findall('measure/note/lyric')],['la','la'])


if __name__=='__main__':unittest.main()

class AssignmentTests(unittest.TestCase):
    def test_shared_note_and_promoted_chord_keep_their_onsets(self):
        chord=b'<note><chord/><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><type>half</type></note>'
        source=SCORE.replace(b'<backup>',chord+b'<backup>',1)
        labels={'P1/1/1':'Ténor','P1/1/2':'Baryton'}
        rules={'P1/0/0':['P1/1/1','P1/1/2'],'P1/0/2':['P1/1/2']}
        parts=ET.fromstring(pupitres.separate(source,labels,rules)).findall('part')
        self.assertIn((0,2,'C','4'),timeline(parts[1]))
        self.assertIn((2,2,'G','4'),timeline(parts[1]))
        self.assertNotIn((2,2,'G','4'),timeline(parts[0]))
        self.assertIn((4,4,'E','4'),timeline(parts[0]))

    def test_shared_rest_and_persistence_and_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'partition-1.musicxml').write_bytes(SCORE)
            labels={'P1/1/1':'Ténor','P1/1/2':'Baryton'}
            rules={'P1/1/1':['P1/1/1','P1/1/2']}
            pupitres.save(folder,'partition-1.musicxml',labels,rules)
            pupitres.save(folder,'partition-1.musicxml',labels)
            self.assertEqual(pupitres.describe(folder,'partition-1.musicxml')['corrections'],rules)
            self.assertIsNotNone(ET.parse(folder/'partition-1.musicxml').find('part/measure[@number="2"]/note/rest'))
            pupitres.save(folder,'partition-1.musicxml',labels,{})
            self.assertEqual(pupitres.describe(folder,'partition-1.musicxml')['corrections'],{})

    def test_invalid_assignments(self):
        labels={'P1/1/1':'Ténor','P1/1/2':'Baryton'}
        for rules in ({'missing':['P1/1/1']},{'P1/0/0':[]},{'P1/0/0':['unknown']},{'P1/0/0':['P1/1/1','P1/1/1']}):
            with self.subTest(rules=rules),self.assertRaises(ValueError):pupitres.separate(SCORE,labels,rules)

"""Name and separate explicitly encoded MusicXML voices; never infer by pitch."""
import io
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from copy import deepcopy

LABELS = ('Soprano', 'Alto', 'Ténor', 'Baryton', 'Basse', 'Accompagnement')


def parse(data):
    if len(data) > 32 * 1024 * 1024 or b'<!ENTITY' in data.upper():
        raise ValueError('Partition trop grande ou format XML non pris en charge.')
    try:root = ET.fromstring(data)
    except ET.ParseError:raise ValueError('MusicXML illisible.') from None
    if root.tag != 'score-partwise' or root.find('part-list') is None:
        raise ValueError('Une partition MusicXML partwise est nécessaire.')
    return root


def discover(data):
    root = parse(data)
    names = {p.get('id', ''): p.findtext('part-name', '') for p in root.findall('part-list/score-part')}
    result = []
    for part in root.findall('part'):
        part_id = part.get('id', '')
        groups = {}
        for note in part.findall('measure/note'):
            if note.find('pitch') is None:continue
            staff, voice = note.findtext('staff', '1'), note.findtext('voice', '1')
            groups.setdefault((staff, voice), 0)
            groups[(staff, voice)] += 1
        for (staff, voice), count in groups.items():
            name = names.get(part_id, '').strip()
            generic = not name or bool(re.fullmatch(r'(voice|voix|part(ie)?|piano|instrument)[ ._-]*\d*', name, re.IGNORECASE))
            label = f'Pupitre {len(result)+1}' if generic else name
            if len(groups) > 1 and not generic:label += f' — ligne {voice}'
            result.append({'id': f'{part_id}/{staff}/{voice}',
                           'part':part_id, 'staff':staff, 'voice':voice,
                           'source_name':name, 'label':label, 'notes':count})
    if len(result) > 64:raise ValueError('Cette partition comporte trop de lignes musicales.')
    return result


def note_catalog(data):
    result=[]
    for part in parse(data).findall('part'):
        part_id = part.get('id', '')
        for mi,measure in enumerate(part.findall('measure')):
            for ni,note in enumerate(measure.findall('note')):
                staff=note.findtext('staff','1')
                pitch=note.find('pitch')
                result.append({'id':f'{part_id}/{mi}/{ni}','part':part_id,'staff':staff,
                    'measure':measure.get('number',str(mi+1)),'index':ni+1,
                    'track':f'{part_id}/{staff}/{note.findtext("voice","1")}',
                    'note':'Silence' if pitch is None else {'C':'Do','D':'Ré','E':'Mi','F':'Fa','G':'Sol','A':'La','B':'Si'}.get(pitch.findtext('step',''),'')+{'-2':'♭♭','-1':'♭','0':'','1':'♯','2':'♯♯'}.get(pitch.findtext('alter',''),'')+pitch.findtext('octave',''),
                    'type':note.findtext('type','mesure')})
    return result


def validate_corrections(data,tracks,corrections):
    if not isinstance(corrections,dict) or len(corrections)>4096:raise ValueError('Affectations invalides.')
    notes={note['id']:note for note in note_catalog(data)}
    known={track['id']:track for track in tracks}
    for key,targets in corrections.items():
        if key not in notes or not isinstance(targets,list) or not 1<=len(targets)<=64 or any(not isinstance(t,str) for t in targets):
            raise ValueError('Affectation de note invalide.')
        if len(set(targets))!=len(targets):raise ValueError('Pupitre répété.')
        note=notes[key]
        for target in targets:
            if target not in known or known[target]['part']!=note['part']:
                raise ValueError('Choisissez des pupitres de la même partie d’origine.')


def merge_parts(root, members):
    """Copy the notes of the other parts into the first member's part, on their own voices."""
    base_id=members[0]['part']
    base=deepcopy(next(p for p in root.findall('part') if p.get('id')==base_id))
    measures=base.findall('measure')
    for part_id in dict.fromkeys(m['part'] for m in members if m['part']!=base_id):
        source=next(p for p in root.findall('part') if p.get('id')==part_id)
        for measure,other in zip(measures,source.findall('measure')):
            position=0
            for element in measure:
                duration=int(element.findtext('duration','0'))
                if element.tag=='note' and element.find('chord') is None:position+=duration
                elif element.tag=='forward':position+=duration
                elif element.tag=='backup':position-=duration
            added=[deepcopy(el) for el in other if el.tag in ('note','forward','backup')]
            if not added:continue
            for element in added:
                voice=element.find('voice')
                if element.tag=='note':
                    if voice is None:
                        voice=ET.Element('voice');duration=element.find('duration')
                        element.insert(list(element).index(duration)+1 if duration is not None else 0,voice)
                    voice.text=f'{part_id}:{voice.text or "1"}'
            if position>0:
                move=ET.Element('backup');ET.SubElement(move,'duration').text=str(position);added.insert(0,move)
            at=len(measure)
            while at>0 and measure[at-1].tag=='barline':at-=1
            for offset,element in enumerate(added):measure.insert(at+offset,element)
    merged=[dict(m,part=base_id,voice=f"{m['part']}:{m['voice']}") if m['part']!=base_id else m for m in members]
    return base,merged


def separate(data, labels, corrections=None):
    tracks = discover(data)
    if not tracks:raise ValueError('Aucune ligne mélodique reconnue.')
    if not isinstance(labels, dict) or set(labels) != {t['id'] for t in tracks}:
        raise ValueError('Renseignez un nom pour chaque ligne reconnue.')
    for value in labels.values():
        if not isinstance(value,str) or not value.strip() or len(value)>80 or any(ord(c)<32 for c in value):
            raise ValueError('Chaque nom de pupitre doit contenir entre 1 et 80 caractères.')
    corrections={} if corrections is None else corrections
    validate_corrections(data,tracks,corrections)
    groups={}
    for track in tracks:
        label=labels[track['id']].strip()
        key=label.casefold()
        group=groups.setdefault(key,[])
        group.append(track)
    root = parse(data); output = deepcopy(root)
    part_list = output.find('part-list')
    if part_list is None:
        raise ValueError('Liste des parties MusicXML manquante.')
    part_list.clear()
    for part in list(output.findall('part')):output.remove(part)
    for i, members in enumerate(groups.values(), 1):
        track=members[0]
        original = next(p for p in root.findall('part') if p.get('id') == track['part'])
        if len({member['part'] for member in members})>1:original,members=merge_parts(root,members)
        voices={(member['staff'],member['voice']):str(index) for index,member in enumerate(members,1)}
        member_voices=set(voices)
        descriptor = next(p for p in root.findall('part-list/score-part') if p.get('id') == track['part'])
        part = deepcopy(original);descriptor = deepcopy(descriptor)
        identifier=f'ChoralePupitre{i}';part.set('id',identifier);descriptor.set('id',identifier)
        part_name = descriptor.find('part-name')
        if part_name is None:
            part_name = ET.SubElement(descriptor, 'part-name')
        part_name.text=labels[track['id']].strip()
        abbreviation=descriptor.find('part-abbreviation')
        if abbreviation is not None:abbreviation.text=labels[track['id']].strip()
        instrument_ids={}
        for j,instrument in enumerate(descriptor.findall('score-instrument'),1):
            old=instrument.get('id')
            new=f'{identifier}-I{j}'
            if old is not None:
                instrument_ids[old]=new
            instrument.set('id',new)
            name=instrument.find('instrument-name')
            if name is not None:name.text=labels[track['id']].strip()
        for instrument in descriptor.findall('midi-instrument'):
            if instrument.get('id') in instrument_ids:instrument.set('id',instrument_ids[instrument.get('id')])
        for mi,measure in enumerate(part.findall('measure')):
            base_selected=False;base_voice=None;base_duration=0;ni=0
            for element in list(measure):
                if element.tag=='attributes':
                    staves=element.find('staves')
                    if staves is not None:staves.text='1'
                    for tag in ('clef','key','time','transpose','staff-details'):
                        for attr in list(element.findall(tag)):
                            number=attr.get('number', '1' if tag in ('clef','staff-details') else None)
                            if number and number!=track['staff']:element.remove(attr)
                            elif number:attr.set('number','1')
                elif element.tag=='note':
                    key=f'{track["part"]}/{mi}/{ni}';ni+=1
                    targets=corrections.get(key)
                    target=next((m for m in members if targets is not None and m['id'] in targets),None)
                    source_key=(element.findtext('staff','1'),element.findtext('voice','1'))
                    selected=bool(target) if targets is not None else source_key in member_voices
                    # Preserve independent rhythmic voices when a note is moved
                    # onto another group. Reusing the target's existing voice
                    # would make a rest and an assigned note overlap in one voice.
                    source_voice=source_key
                    if source_voice not in voices:voices[source_voice]=str(len(voices)+1)
                    chosen_voice=voices[source_voice]
                    chord_element=element.find('chord')
                    chord=chord_element is not None
                    duration_value=int(element.findtext('duration','0'))
                    if selected:
                        if chord and (not base_selected or base_voice!=chosen_voice):
                            index=list(measure).index(element)
                            if base_duration:
                                move=ET.Element('backup');ET.SubElement(move,'duration').text=str(base_duration);measure.insert(index,move)
                            if chord_element is not None:
                                element.remove(chord_element)
                            delta=base_duration-duration_value
                            if delta:
                                move=ET.Element('forward' if delta>0 else 'backup');ET.SubElement(move,'duration').text=str(abs(delta));measure.insert(list(measure).index(element)+1,move)
                        staff=element.find('staff')
                        if staff is not None:staff.text='1'
                        voice=element.find('voice')
                        if voice is None:
                            voice=ET.Element('voice');duration=element.find('duration');element.insert(list(element).index(duration)+1 if duration is not None else 0,voice)
                        voice.text=chosen_voice
                        for instrument in element.findall('instrument'):
                            if instrument.get('id') in instrument_ids:instrument.set('id',instrument_ids[instrument.get('id')])
                    else:
                        index=list(measure).index(element);measure.remove(element)
                        duration=element.find('duration')
                        if not chord and duration is not None and float(duration.text or 0)>0:
                            forward=ET.Element('forward');forward.append(deepcopy(duration));measure.insert(index,forward)
                    if not chord:base_selected=selected;base_voice=chosen_voice if selected else None;base_duration=duration_value
                elif element.tag=='direction':
                    staff=element.find('staff')
                    if staff is not None:
                        if staff.text!=track['staff']:measure.remove(element)
                        else:staff.text='1'
                elif element.tag in ('backup','forward'):
                    base_selected=False
                    for tag in ('voice','staff'):
                        child=element.find(tag)
                        if child is not None:child.text='1'
            # A voice may be absent for entire measures. Forwards alone keep
            # playback time, but provide no staff entry for lyric layout/cursors.
            # Materialize that silence without changing the measure's duration.
            if not measure.findall('note'):
                position=end=0
                for element in measure:
                    duration=int(element.findtext('duration','0'))
                    if element.tag=='forward':position+=duration;end=max(end,position)
                    elif element.tag=='backup':position-=duration
                if end>0:
                    if position:
                        move=ET.SubElement(measure,'backup' if position>0 else 'forward')
                        ET.SubElement(move,'duration').text=str(abs(position))
                    rest=ET.SubElement(measure,'note')
                    ET.SubElement(rest,'rest',measure='yes')
                    ET.SubElement(rest,'duration').text=str(end)
                    ET.SubElement(rest,'voice').text='1'
        part_list.append(descriptor);output.append(part)
    identification=output.find('identification')
    if identification is None:
        identification=ET.Element('identification')
        index=next((i for i,el in enumerate(output) if el.tag in ('defaults','credit','part-list')),len(output))
        output.insert(index,identification)
    miscellaneous=identification.find('miscellaneous')
    if miscellaneous is None:miscellaneous=ET.SubElement(identification,'miscellaneous')
    for field in list(miscellaneous.findall('miscellaneous-field')):
        if field.get('name')=='chorale-grouped-parts':miscellaneous.remove(field)
    ET.SubElement(miscellaneous,'miscellaneous-field',name='chorale-grouped-parts').text=','.join(p.get('id', '') for p in output.findall('part'))
    return ET.tostring(output,encoding='utf-8',xml_declaration=True)


def source(folder, filename):
    if not re.fullmatch(r'partition-[1-9][0-9]*\.musicxml',filename):raise ValueError('Fichier invalide.')
    path=folder/filename
    if not path.is_file():raise ValueError('Partition introuvable.')
    original=folder/'originals'/filename
    return original.read_bytes() if original.is_file() else path.read_bytes()


def describe(folder, filename):
    tracks=discover(source(folder,filename))
    try:stored=json.loads((folder/'pupitres.json').read_text()).get(filename,{})
    except (OSError,ValueError):stored={}
    for track in tracks:track['label']=stored.get(track['id'],track['label'])
    try:corrections=json.loads((folder/'affectations.json').read_text()).get(filename,{})
    except (OSError,ValueError):corrections={}
    return {'file':filename,'tracks':tracks,'choices':LABELS,'notes':note_catalog(source(folder,filename)),'corrections':corrections}


def save(folder, filename, labels, corrections=None):
    try:assignments=json.loads((folder/'affectations.json').read_text())
    except (OSError,ValueError):assignments={}
    if corrections is None:corrections=assignments.get(filename,{})
    original=source(folder,filename);xml=separate(original,labels,corrections)
    path=folder/filename;mxl=path.with_suffix('.mxl')
    assignments[filename]=corrections
    changes={path:xml,folder/'affectations.json':json.dumps(assignments,ensure_ascii=False).encode()}
    if mxl.exists():
        buffer=io.BytesIO()
        with zipfile.ZipFile(mxl) as archive,zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as updated:
            container=ET.fromstring(archive.read('META-INF/container.xml'))
            member=next(el.get('full-path') for el in container.iter() if el.tag.split('}')[-1]=='rootfile')
            if member not in archive.namelist():raise ValueError('Archive MXL invalide.')
            for info in archive.infolist():updated.writestr(info,xml if info.filename==member else archive.read(info.filename))
        changes[mxl]=buffer.getvalue()
    try:settings=json.loads((folder/'pupitres.json').read_text())
    except (OSError,ValueError):settings={}
    settings[filename]={key:value.strip() for key,value in labels.items()}
    changes[folder/'pupitres.json']=json.dumps(settings,ensure_ascii=False).encode()
    originals=folder/'originals';originals.mkdir(exist_ok=True,mode=0o700)
    backup=originals/filename
    if not backup.exists():backup.write_bytes(original)
    previous={p:p.read_bytes() if p.exists() else None for p in changes}
    try:
        for p,data in changes.items():
            temporary=p.with_name(p.name+'.tmp');temporary.write_bytes(data);os.replace(temporary,p)
    except OSError:
        for p,data in previous.items():
            if data is not None:p.write_bytes(data)
            elif p.exists():p.unlink()
        raise
    return describe(folder,filename)

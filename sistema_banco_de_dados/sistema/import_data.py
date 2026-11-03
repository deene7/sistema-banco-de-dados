"""Leitura e validação de arquivos. Nenhuma instrução contida no arquivo é executada."""
import codecs
import csv
import re
import unicodedata
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FIELDS = {
 'cpf':'CPF', 'nome':'Nome completo', 'nome_mae':'Nome da mãe', 'data_nascimento':'Nascimento',
 'sexo_origem':'Sexo (código)', 'telefone1':'Telefone 1', 'telefone2':'Telefone 2',
 'telefone3':'Telefone 3', 'ddd1':'DDD do telefone 1', 'ddd2':'DDD do telefone 2',
 'ddd3':'DDD do telefone 3', 'email1':'E-mail 1', 'email2':'E-mail 2', 'email3':'E-mail 3',
 'tipo':'Tipo de via', 'titulo':'Título da via', 'logradouro':'Logradouro', 'numero':'Número do imóvel',
 'complemento':'Complemento', 'bairro':'Bairro', 'cidade':'Cidade', 'uf':'Estado (UF)', 'cep':'CEP',
 'extra':'Preservar como informação da origem'}
ALIASES = {
 'cpf': ('cpf','cpfcliente','documento','numerocpf'),
 'nome': ('nome','nomecliente','nomecompleto','cliente'),
 'nome_mae': ('nomemae','mae','nomedamae'),
 'data_nascimento': ('nasc','dtnasc','datanascimento','dtnascimento','nascimento','datanasc'),
 'sexo_origem': ('sexo','genero'),
 'telefone1': ('telefone','telefone1','celular','celular1','cel1','fone','fone1','tel','tel1','contato','whatsapp'),
 'telefone2': ('telefone2','celular2','cel2','fone2'), 'telefone3': ('telefone3','celular3','cel3','fone3'),
 'ddd1': ('ddd','ddd1','dddcel1','dddtelefone','dddtelefone1','dddcelular1'),
 'ddd2': ('ddd2','dddcel2','dddtelefone2','dddcelular2'), 'ddd3': ('ddd3','dddcel3','dddtelefone3','dddcelular3'),
 'email1': ('email','email1','correioeletronico'), 'email2': ('email2',), 'email3': ('email3',),
 'tipo': ('tipo','tipologradouro'), 'titulo': ('titulo','titulologradouro'),
 'logradouro': ('logradouro','endereco','rua'), 'numero': ('numero','numeroimovel','num','nro'),
 'complemento': ('complemento','compl'), 'bairro': ('bairro',),
 'cidade': ('cidade','municipio'), 'uf': ('uf','estado'), 'cep': ('cep','codigopostal')}
ADDRESS_FIELDS = ('tipo','titulo','logradouro','numero','complemento','bairro','cidade','uf','cep')
for _n in range(4,11):
    FIELDS['telefone'+str(_n)]='Telefone '+str(_n)
    FIELDS['ddd'+str(_n)]='DDD do telefone '+str(_n)
    FIELDS['email'+str(_n)]='E-mail '+str(_n)
    ALIASES['telefone'+str(_n)]=tuple(prefix+str(_n) for prefix in ('telefone','celular','cel','fone','tel'))
    ALIASES['ddd'+str(_n)]=tuple(prefix+str(_n) for prefix in ('ddd','dddcel','dddtelefone','dddcelular'))
    ALIASES['email'+str(_n)]=('email'+str(_n),)
LIMITS = {'nome':255,'nome_mae':255,'tipo':100,'titulo':100,'logradouro':255,'numero':50,
          'complemento':255,'bairro':150,'cidade':150,'uf':2,'cep':8}


def key(value):
    return re.sub('[^a-z0-9]', '', unicodedata.normalize('NFKD', str(value)).encode('ascii','ignore').decode().lower())


def guess(headers):
    aliases = {alias: field for field, names in ALIASES.items() for alias in names}
    normalized_headers={key(header) for header in headers}
    if 'cel1' in normalized_headers and 'fone1' in normalized_headers:
        aliases['fone1']='telefone3'
        if 'dddcel1' in normalized_headers:aliases['ddd1']='ddd3'
    result, used = [], set()
    for header in headers:
        target = aliases.get(key(header),'extra')
        if target != 'extra' and target in used:
            target = 'extra'
        if target != 'extra': used.add(target)
        result.append(target)
    return result


def cell(value):
    if value is None: return ''
    if isinstance(value, (datetime,date)): return value.isoformat()
    if isinstance(value,float) and value.is_integer(): return str(int(value))
    return str(value).strip()


def csv_format(path):
    with open(path,'rb') as stream: sample=stream.read(65536)
    try:
        text=codecs.getincrementaldecoder('utf-8-sig')().decode(sample,final=False)
        encoding='utf-8-sig'
    except UnicodeDecodeError:
        encoding='cp1252'
        text=sample.decode(encoding)
    try: delimiter=csv.Sniffer().sniff(text,delimiters=';,\t|').delimiter
    except csv.Error: delimiter=';'
    return encoding,delimiter


def rows(path, sheet=None):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        encoding,delimiter=csv_format(path)
        csv.field_size_limit(1000000)
        with path.open(encoding=encoding,newline='') as stream:
            for row in csv.reader(stream,delimiter=delimiter): yield [cell(v) for v in row]
    else:
        from openpyxl import load_workbook
        with zipfile.ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > 250*1024*1024:
                raise ValueError('O Excel descompactado excede o limite de 250 MB. Divida o arquivo.')
        # data_only=False permite recusar fórmulas em identificadores/campos, sem usar caches desatualizados.
        book=load_workbook(path,read_only=True,data_only=False,keep_links=False)
        try:
            worksheet=book[sheet] if sheet and sheet in book.sheetnames else book.worksheets[0]
            for row in worksheet.iter_rows(values_only=True): yield [cell(v) for v in row]
        finally: book.close()


def inspect_file(path, sheet=None):
    sheets=[]
    if Path(path).suffix.lower()=='.xlsx':
        with zipfile.ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist())>250*1024*1024:
                raise ValueError('O Excel descompactado excede o limite de 250 MB. Divida o arquivo.')
        from openpyxl import load_workbook
        book=load_workbook(path,read_only=True,data_only=False,keep_links=False)
        sheets=book.sheetnames
        book.close()
        if sheet and sheet not in sheets: raise ValueError('A aba selecionada não existe no arquivo.')
        sheet=sheet or sheets[0]
    iterator=rows(path,sheet)
    try:
        headers=next(iterator,[])
        if not headers or len(headers)>200: raise ValueError('Use um cabeçalho na primeira linha, com até 200 colunas.')
        examples=[]
        for _, row in zip(range(3), iterator): examples.append(row)
    finally: iterator.close()
    return headers,examples,sheets,sheet


def valid_cpf(value):
    return bool(re.fullmatch('[0-9]{11}',value)) and len(set(value))>1 and all(
        int(value[n])==(sum(int(value[i])*(n+1-i) for i in range(n))*10%11)%10 for n in (9,10))


def parse_birth(value):
    if not value:return None
    for fmt in ('%Y%m%d','%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%Y-%m-%dT%H:%M:%S','%d/%m/%Y %H:%M:%S'):
        try:
            result=datetime.strptime(value,fmt).date()
            if not 1800<=result.year or result>datetime.now(timezone(timedelta(hours=-3))).date():break
            return result.isoformat()
        except ValueError:pass
    raise ValueError('Data de nascimento inválida ou em formato não reconhecido.')


def normalize(row, mapping):
    values={target:cell(value) for target,value in zip(mapping,row) if target!='extra'}
    errors=[];warnings=[]
    for target,value in list(values.items()):
        if value.startswith('='):
            message=f'Fórmula em {FIELDS[target]}. Substitua por um valor.'
            (errors if target in ('cpf','nome') else warnings).append(message)
            values[target]='' 
    raw=values.get('cpf','')
    if not raw or any(c.isalpha() for c in raw):
        cpf=None
    else:
        d=re.sub('[^0-9]','',raw)
        cpf=d.zfill(11) if 1<=len(d)<=11 else None
    if not cpf or not valid_cpf(cpf):errors.append('CPF ausente, inválido ou ambíguo.')
    data={'cpf':cpf,'fields':{},'phones':[],'emails':[],'addresses':[]}
    for name in ('nome','nome_mae'):
        value=' '.join(values.get(name,'').split())
        if len(value)>LIMITS[name]:(errors if name=='nome' else warnings).append(f'{FIELDS[name]} excede {LIMITS[name]} caracteres.')
        elif value:data['fields'][name]=value
    try:
        birthday=parse_birth(values.get('data_nascimento',''))
        if birthday:data['fields']['data_nascimento']=birthday
    except ValueError as error:warnings.append(str(error))
    sex=values.get('sexo_origem','').upper()
    if sex in ('MASCULINO','MASC'):sex='M'
    if sex in ('FEMININO','FEM'):sex='F'
    if sex and sex not in ('M','F','I'):warnings.append('Sexo fora dos códigos reconhecidos; revise a origem.')
    elif sex:data['fields']['sexo_origem']=sex
    for n in range(1,11):
        rawphone=values.get('telefone'+str(n),'')
        if rawphone:
            d=re.sub('[^0-9]','',rawphone)
            ddd=re.sub('[^0-9]','',values.get('ddd'+str(n),''))
            if len(d) in (8,9) and len(ddd)==2:d=ddd+d
            if len(d) in (12,13) and d.startswith('55'):d=d[2:]
            if any(c.isalpha() for c in rawphone) or len(d) not in (10,11) or d.startswith('0') or len(set(d))==1:
                warnings.append(f'Telefone {n} inválido ou sem DDD.')
            elif d not in data['phones']:data['phones'].append(d)
        for email in re.split('[;,\n]+',values.get('email'+str(n),'')):
            email=email.strip()
            if not email:continue
            if len(email)>320 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):warnings.append(f'E-mail {n} com formato inválido.')
            elif email not in data['emails']:data['emails'].append(email)
    address={name:values.get(name,'').strip() or None for name in ADDRESS_FIELDS}
    if any(address.values()):
        if address['uf']:
            address['uf']=address['uf'].upper()
            if not re.fullmatch('[A-Z]{2}',address['uf']):
                warnings.append('UF inválida; campo não aproveitado.');address['uf']=None
        if address['cep']:
            d=re.sub('[^0-9]','',address['cep'])
            if any(c.isalpha() for c in address['cep']) or not 1<=len(d)<=8:
                warnings.append('CEP inválido; campo não aproveitado.');address['cep']=None
            else:address['cep']=d.zfill(8)
        for name,value in address.items():
            if value and len(value)>LIMITS[name]:
                warnings.append(f'{FIELDS[name]} excede o tamanho aceito; campo não aproveitado.');address[name]=None
        if address['logradouro']:data['addresses'].append(address)
        else:warnings.append('Endereço sem logradouro válido; endereço preservado somente na origem.')
    data['warnings']=list(dict.fromkeys(warnings))
    return data,list(dict.fromkeys(errors)),bool(cpf and valid_cpf(cpf) and len(re.sub('[^0-9]','',raw))<11)


def same(field,a,b):
    if a in (None,'') or b in (None,''):return True
    if field=='data_nascimento':return str(a)[:10]==str(b)[:10]
    return ' '.join(str(a).upper().split())==' '.join(str(b).upper().split())

"""Build ../cities.json from the curated list below, validating every entry.

Run:  python tools/build_cities.py

Each line: name ; ISO country ; IANA tz ; lat ; lon ; holiday subdivision (or -) ; label
Checks per city:
  - the tz loads in zoneinfo and is listed for that country in tzdata's zone.tab
    (or is a documented alias), so a typo'd zone or country is caught;
  - lon / 15 is within 3.5 h of the zone's standard UTC offset (catches swapped or
    mistyped coordinates);
  - when astral's built-in geocoder knows the city, the coordinates agree within 0.6 deg;
  - holidays.country_holidays(country, subdiv) loads; an unsupported subdivision falls
    back to the whole country, an unsupported country gets holidays = null.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import holidays
import tzdata
from astral import geocoder

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "cities.json"

CITIES = """
# ---- North America
Los Angeles;US;America/Los_Angeles;34.0522;-118.2437;CA;LA
New York;US;America/New_York;40.7128;-74.0060;NY;NY
San Francisco;US;America/Los_Angeles;37.7749;-122.4194;CA;SF
Chicago;US;America/Chicago;41.8781;-87.6298;IL;CHI
Seattle;US;America/Los_Angeles;47.6062;-122.3321;WA;SEA
Boston;US;America/New_York;42.3601;-71.0589;MA;BOS
Washington;US;America/New_York;38.9072;-77.0369;DC;DC
Miami;US;America/New_York;25.7617;-80.1918;FL;MIA
Atlanta;US;America/New_York;33.7490;-84.3880;GA;ATL
Dallas;US;America/Chicago;32.7767;-96.7970;TX;DAL
Houston;US;America/Chicago;29.7604;-95.3698;TX;HOU
Austin;US;America/Chicago;30.2672;-97.7431;TX;AUS
Denver;US;America/Denver;39.7392;-104.9903;CO;DEN
Phoenix;US;America/Phoenix;33.4484;-112.0740;AZ;PHX
Las Vegas;US;America/Los_Angeles;36.1699;-115.1398;NV;LV
San Diego;US;America/Los_Angeles;32.7157;-117.1611;CA;SD
Philadelphia;US;America/New_York;39.9526;-75.1652;PA;PHL
Detroit;US;America/Detroit;42.3314;-83.0458;MI;DET
Minneapolis;US;America/Chicago;44.9778;-93.2650;MN;MSP
Portland;US;America/Los_Angeles;45.5152;-122.6784;OR;PDX
Nashville;US;America/Chicago;36.1627;-86.7816;TN;BNA
New Orleans;US;America/Chicago;29.9511;-90.0715;LA;NOL
Salt Lake City;US;America/Denver;40.7608;-111.8910;UT;SLC
Honolulu;US;Pacific/Honolulu;21.3069;-157.8583;HI;HNL
Anchorage;US;America/Anchorage;61.2181;-149.9003;AK;ANC
Toronto;CA;America/Toronto;43.6532;-79.3832;ON;TOR
Montreal;CA;America/Toronto;45.5017;-73.5673;QC;MTL
Vancouver;CA;America/Vancouver;49.2827;-123.1207;BC;VAN
Calgary;CA;America/Edmonton;51.0447;-114.0719;AB;YYC
Edmonton;CA;America/Edmonton;53.5461;-113.4938;AB;EDM
Ottawa;CA;America/Toronto;45.4215;-75.6972;ON;OTT
Winnipeg;CA;America/Winnipeg;49.8951;-97.1384;MB;WPG
Halifax;CA;America/Halifax;44.6488;-63.5752;NS;HAL
Mexico City;MX;America/Mexico_City;19.4326;-99.1332;-;MEX
Guadalajara;MX;America/Mexico_City;20.6597;-103.3496;-;GDL
Monterrey;MX;America/Monterrey;25.6866;-100.3161;-;MTY
Cancún;MX;America/Cancun;21.1619;-86.8515;-;CUN
Nuuk;GL;America/Nuuk;64.1814;-51.6941;-;NUK
# ---- Central America & Caribbean
Guatemala City;GT;America/Guatemala;14.6349;-90.5069;-;GUA
San Salvador;SV;America/El_Salvador;13.6929;-89.2182;-;SAL
Tegucigalpa;HN;America/Tegucigalpa;14.0723;-87.1921;-;TGU
Managua;NI;America/Managua;12.1364;-86.2514;-;MGA
San José;CR;America/Costa_Rica;9.9281;-84.0907;-;SJO
Panama City;PA;America/Panama;8.9824;-79.5199;-;PTY
Havana;CU;America/Havana;23.1136;-82.3666;-;HAV
Kingston;JM;America/Jamaica;17.9714;-76.7936;-;KIN
Santo Domingo;DO;America/Santo_Domingo;18.4861;-69.9312;-;SDQ
San Juan;PR;America/Puerto_Rico;18.4655;-66.1057;-;SJU
Port of Spain;TT;America/Port_of_Spain;10.6549;-61.5019;-;POS
Nassau;BS;America/Nassau;25.0443;-77.3504;-;NAS
Bridgetown;BB;America/Barbados;13.0975;-59.6167;-;BGI
# ---- South America
São Paulo;BR;America/Sao_Paulo;-23.5505;-46.6333;SP;SAO
Rio de Janeiro;BR;America/Sao_Paulo;-22.9068;-43.1729;RJ;RIO
Brasília;BR;America/Sao_Paulo;-15.7939;-47.8828;DF;BSB
Buenos Aires;AR;America/Argentina/Buenos_Aires;-34.6037;-58.3816;-;BUE
Santiago;CL;America/Santiago;-33.4489;-70.6693;-;SCL
Lima;PE;America/Lima;-12.0464;-77.0428;-;LIM
Bogotá;CO;America/Bogota;4.7110;-74.0721;-;BOG
Medellín;CO;America/Bogota;6.2442;-75.5812;-;MDE
Caracas;VE;America/Caracas;10.4806;-66.9036;-;CCS
Quito;EC;America/Guayaquil;-0.1807;-78.4678;-;UIO
Guayaquil;EC;America/Guayaquil;-2.1709;-79.9224;-;GYE
La Paz;BO;America/La_Paz;-16.4897;-68.1193;-;LPB
Asunción;PY;America/Asuncion;-25.2637;-57.5759;-;ASU
Montevideo;UY;America/Montevideo;-34.9011;-56.1645;-;MVD
Georgetown;GY;America/Guyana;6.8013;-58.1551;-;GEO
Paramaribo;SR;America/Paramaribo;5.8520;-55.2038;-;PBM
# ---- Europe
London;GB;Europe/London;51.5074;-0.1278;ENG;LON
Manchester;GB;Europe/London;53.4808;-2.2426;ENG;MAN
Edinburgh;GB;Europe/London;55.9533;-3.1883;SCT;EDI
Glasgow;GB;Europe/London;55.8642;-4.2518;SCT;GLA
Cardiff;GB;Europe/London;51.4816;-3.1791;WLS;CWL
Belfast;GB;Europe/London;54.5973;-5.9301;NIR;BFS
Dublin;IE;Europe/Dublin;53.3498;-6.2603;-;DUB
Paris;FR;Europe/Paris;48.8566;2.3522;-;PAR
Lyon;FR;Europe/Paris;45.7640;4.8357;-;LYS
Marseille;FR;Europe/Paris;43.2965;5.3698;-;MRS
Nice;FR;Europe/Paris;43.7102;7.2620;-;NCE
Berlin;DE;Europe/Berlin;52.5200;13.4050;BE;BER
Munich;DE;Europe/Berlin;48.1351;11.5820;BY;MUC
Hamburg;DE;Europe/Berlin;53.5511;9.9937;HH;HAM
Frankfurt;DE;Europe/Berlin;50.1109;8.6821;HE;FRA
Cologne;DE;Europe/Berlin;50.9375;6.9603;NW;CGN
Düsseldorf;DE;Europe/Berlin;51.2277;6.7735;NW;DUS
Stuttgart;DE;Europe/Berlin;48.7758;9.1829;BW;STR
Leipzig;DE;Europe/Berlin;51.3397;12.3731;SN;LEJ
Amsterdam;NL;Europe/Amsterdam;52.3676;4.9041;-;AMS
Rotterdam;NL;Europe/Amsterdam;51.9244;4.4777;-;RTM
The Hague;NL;Europe/Amsterdam;52.0705;4.3007;-;HAG
Brussels;BE;Europe/Brussels;50.8503;4.3517;-;BRU
Antwerp;BE;Europe/Brussels;51.2194;4.4025;-;ANR
Luxembourg;LU;Europe/Luxembourg;49.6116;6.1319;-;LUX
Zurich;CH;Europe/Zurich;47.3769;8.5417;ZH;ZRH
Geneva;CH;Europe/Zurich;46.2044;6.1432;GE;GVA
Basel;CH;Europe/Zurich;47.5596;7.5886;BS;BSL
Bern;CH;Europe/Zurich;46.9480;7.4474;BE;BRN
Lausanne;CH;Europe/Zurich;46.5197;6.6323;VD;LSN
Vienna;AT;Europe/Vienna;48.2082;16.3738;9;VIE
Salzburg;AT;Europe/Vienna;47.8095;13.0550;5;SZG
Madrid;ES;Europe/Madrid;40.4168;-3.7038;MD;MAD
Barcelona;ES;Europe/Madrid;41.3851;2.1734;CT;BCN
Valencia;ES;Europe/Madrid;39.4699;-0.3763;VC;VLC
Seville;ES;Europe/Madrid;37.3891;-5.9845;AN;SVQ
Málaga;ES;Europe/Madrid;36.7213;-4.4214;AN;AGP
Bilbao;ES;Europe/Madrid;43.2630;-2.9350;PV;BIO
Palma;ES;Europe/Madrid;39.5696;2.6502;IB;PMI
Las Palmas;ES;Atlantic/Canary;28.1235;-15.4363;CN;LPA
Lisbon;PT;Europe/Lisbon;38.7223;-9.1393;-;LIS
Porto;PT;Europe/Lisbon;41.1579;-8.6291;-;OPO
Rome;IT;Europe/Rome;41.9028;12.4964;-;ROM
Milan;IT;Europe/Rome;45.4642;9.1900;-;MIL
Naples;IT;Europe/Rome;40.8518;14.2681;-;NAP
Florence;IT;Europe/Rome;43.7696;11.2558;-;FLR
Turin;IT;Europe/Rome;45.0703;7.6869;-;TRN
Venice;IT;Europe/Rome;45.4408;12.3155;-;VCE
Athens;GR;Europe/Athens;37.9838;23.7275;-;ATH
Thessaloniki;GR;Europe/Athens;40.6401;22.9444;-;SKG
Copenhagen;DK;Europe/Copenhagen;55.6761;12.5683;-;CPH
Stockholm;SE;Europe/Stockholm;59.3293;18.0686;-;STO
Gothenburg;SE;Europe/Stockholm;57.7089;11.9746;-;GOT
Oslo;NO;Europe/Oslo;59.9139;10.7522;-;OSL
Bergen;NO;Europe/Oslo;60.3913;5.3221;-;BGO
Helsinki;FI;Europe/Helsinki;60.1699;24.9384;-;HEL
Reykjavík;IS;Atlantic/Reykjavik;64.1466;-21.9426;-;REK
Tallinn;EE;Europe/Tallinn;59.4370;24.7536;-;TLL
Riga;LV;Europe/Riga;56.9496;24.1052;-;RIX
Vilnius;LT;Europe/Vilnius;54.6872;25.2797;-;VNO
Warsaw;PL;Europe/Warsaw;52.2297;21.0122;-;WAW
Kraków;PL;Europe/Warsaw;50.0647;19.9450;-;KRK
Gdańsk;PL;Europe/Warsaw;54.3520;18.6466;-;GDN
Wrocław;PL;Europe/Warsaw;51.1079;17.0385;-;WRO
Prague;CZ;Europe/Prague;50.0755;14.4378;-;PRG
Brno;CZ;Europe/Prague;49.1951;16.6068;-;BRQ
Bratislava;SK;Europe/Bratislava;48.1486;17.1077;-;BTS
Budapest;HU;Europe/Budapest;47.4979;19.0402;-;BUD
Ljubljana;SI;Europe/Ljubljana;46.0569;14.5058;-;LJU
Zagreb;HR;Europe/Zagreb;45.8150;15.9819;-;ZAG
Split;HR;Europe/Zagreb;43.5081;16.4402;-;SPU
Belgrade;RS;Europe/Belgrade;44.7866;20.4489;-;BEG
Sarajevo;BA;Europe/Sarajevo;43.8563;18.4131;-;SJJ
Podgorica;ME;Europe/Podgorica;42.4304;19.2594;-;TGD
Skopje;MK;Europe/Skopje;41.9981;21.4254;-;SKP
Tirana;AL;Europe/Tirane;41.3275;19.8187;-;TIA
Sofia;BG;Europe/Sofia;42.6977;23.3219;-;SOF
Bucharest;RO;Europe/Bucharest;44.4268;26.1025;-;BUH
Cluj-Napoca;RO;Europe/Bucharest;46.7712;23.6236;-;CLJ
Chișinău;MD;Europe/Chisinau;47.0105;28.8638;-;KIV
Kyiv;UA;Europe/Kyiv;50.4501;30.5234;-;KYV
Lviv;UA;Europe/Kyiv;49.8397;24.0297;-;LWO
Odesa;UA;Europe/Kyiv;46.4825;30.7233;-;ODS
Minsk;BY;Europe/Minsk;53.9006;27.5590;-;MSQ
Moscow;RU;Europe/Moscow;55.7558;37.6173;-;MOW
Saint Petersburg;RU;Europe/Moscow;59.9311;30.3609;-;LED
Yekaterinburg;RU;Asia/Yekaterinburg;56.8389;60.6057;-;SVX
Novosibirsk;RU;Asia/Novosibirsk;55.0084;82.9357;-;OVB
Vladivostok;RU;Asia/Vladivostok;43.1155;131.8855;-;VVO
Istanbul;TR;Europe/Istanbul;41.0082;28.9784;-;IST
Ankara;TR;Europe/Istanbul;39.9334;32.8597;-;ANK
Izmir;TR;Europe/Istanbul;38.4237;27.1428;-;IZM
Nicosia;CY;Asia/Nicosia;35.1856;33.3823;-;NIC
Valletta;MT;Europe/Malta;35.8989;14.5146;-;MLA
Monaco;MC;Europe/Monaco;43.7384;7.4246;-;MCM
Andorra la Vella;AD;Europe/Andorra;42.5063;1.5218;-;ALV
Vaduz;LI;Europe/Vaduz;47.1410;9.5209;-;VDZ
San Marino;SM;Europe/San_Marino;43.9424;12.4578;-;SMR
Vatican City;VA;Europe/Vatican;41.9029;12.4534;-;VAT
Tbilisi;GE;Asia/Tbilisi;41.7151;44.8271;-;TBS
Yerevan;AM;Asia/Yerevan;40.1792;44.4991;-;EVN
Baku;AZ;Asia/Baku;40.4093;49.8671;-;BAK
# ---- Middle East
Dubai;AE;Asia/Dubai;25.2048;55.2708;-;DXB
Abu Dhabi;AE;Asia/Dubai;24.4539;54.3773;-;AUH
Doha;QA;Asia/Qatar;25.2854;51.5310;-;DOH
Riyadh;SA;Asia/Riyadh;24.7136;46.6753;-;RUH
Jeddah;SA;Asia/Riyadh;21.4858;39.1925;-;JED
Kuwait City;KW;Asia/Kuwait;29.3759;47.9774;-;KWI
Manama;BH;Asia/Bahrain;26.2285;50.5860;-;BAH
Muscat;OM;Asia/Muscat;23.5880;58.3829;-;MCT
Tel Aviv;IL;Asia/Jerusalem;32.0853;34.7818;-;TLV
Jerusalem;IL;Asia/Jerusalem;31.7683;35.2137;-;JRS
Amman;JO;Asia/Amman;31.9539;35.9106;-;AMM
Beirut;LB;Asia/Beirut;33.8938;35.5018;-;BEY
Damascus;SY;Asia/Damascus;33.5138;36.2765;-;DAM
Baghdad;IQ;Asia/Baghdad;33.3152;44.3661;-;BGW
Tehran;IR;Asia/Tehran;35.6892;51.3890;-;THR
Sanaa;YE;Asia/Aden;15.3694;44.1910;-;SAH
Kabul;AF;Asia/Kabul;34.5553;69.2075;-;KBL
# ---- Africa
Cairo;EG;Africa/Cairo;30.0444;31.2357;-;CAI
Alexandria;EG;Africa/Cairo;31.2001;29.9187;-;ALY
Casablanca;MA;Africa/Casablanca;33.5731;-7.5898;-;CAS
Rabat;MA;Africa/Casablanca;34.0209;-6.8416;-;RBA
Marrakesh;MA;Africa/Casablanca;31.6295;-7.9811;-;RAK
Algiers;DZ;Africa/Algiers;36.7538;3.0588;-;ALG
Tunis;TN;Africa/Tunis;36.8065;10.1815;-;TUN
Tripoli;LY;Africa/Tripoli;32.8872;13.1913;-;TIP
Khartoum;SD;Africa/Khartoum;15.5007;32.5599;-;KRT
Addis Ababa;ET;Africa/Addis_Ababa;9.0300;38.7400;-;ADD
Nairobi;KE;Africa/Nairobi;-1.2921;36.8219;-;NBO
Mombasa;KE;Africa/Nairobi;-4.0435;39.6682;-;MBA
Kampala;UG;Africa/Kampala;0.3476;32.5825;-;KLA
Kigali;RW;Africa/Kigali;-1.9441;30.0619;-;KGL
Dar es Salaam;TZ;Africa/Dar_es_Salaam;-6.7924;39.2083;-;DAR
Dodoma;TZ;Africa/Dar_es_Salaam;-6.1630;35.7516;-;DOD
Lagos;NG;Africa/Lagos;6.5244;3.3792;-;LOS
Abuja;NG;Africa/Lagos;9.0765;7.3986;-;ABV
Accra;GH;Africa/Accra;5.6037;-0.1870;-;ACC
Abidjan;CI;Africa/Abidjan;5.3600;-4.0083;-;ABJ
Dakar;SN;Africa/Dakar;14.7167;-17.4677;-;DKR
Bamako;ML;Africa/Bamako;12.6392;-8.0029;-;BKO
Ouagadougou;BF;Africa/Ouagadougou;12.3714;-1.5197;-;OUA
Niamey;NE;Africa/Niamey;13.5116;2.1254;-;NIM
N'Djamena;TD;Africa/Ndjamena;12.1348;15.0557;-;NDJ
Yaoundé;CM;Africa/Douala;3.8480;11.5021;-;YAO
Douala;CM;Africa/Douala;4.0511;9.7679;-;DLA
Kinshasa;CD;Africa/Kinshasa;-4.4419;15.2663;-;FIH
Brazzaville;CG;Africa/Brazzaville;-4.2634;15.2429;-;BZV
Libreville;GA;Africa/Libreville;0.4162;9.4673;-;LBV
Luanda;AO;Africa/Luanda;-8.8390;13.2894;-;LAD
Lusaka;ZM;Africa/Lusaka;-15.3875;28.3228;-;LUN
Harare;ZW;Africa/Harare;-17.8252;31.0335;-;HRE
Lilongwe;MW;Africa/Blantyre;-13.9626;33.7741;-;LLW
Maputo;MZ;Africa/Maputo;-25.9692;32.5732;-;MPM
Windhoek;NA;Africa/Windhoek;-22.5609;17.0658;-;WDH
Gaborone;BW;Africa/Gaborone;-24.6282;25.9231;-;GBE
Johannesburg;ZA;Africa/Johannesburg;-26.2041;28.0473;-;JNB
Pretoria;ZA;Africa/Johannesburg;-25.7479;28.2293;-;PRY
Cape Town;ZA;Africa/Johannesburg;-33.9249;18.4241;-;CPT
Durban;ZA;Africa/Johannesburg;-29.8587;31.0218;-;DUR
Antananarivo;MG;Indian/Antananarivo;-18.8792;47.5079;-;TNR
Port Louis;MU;Indian/Mauritius;-20.1609;57.5012;-;MRU
Mogadishu;SO;Africa/Mogadishu;2.0469;45.3182;-;MGQ
Djibouti;DJ;Africa/Djibouti;11.5721;43.1456;-;JIB
Asmara;ER;Africa/Asmara;15.3229;38.9251;-;ASM
Juba;SS;Africa/Juba;4.8594;31.5713;-;JUB
Freetown;SL;Africa/Freetown;8.4657;-13.2317;-;FNA
Monrovia;LR;Africa/Monrovia;6.3156;-10.8074;-;MLW
Conakry;GN;Africa/Conakry;9.6412;-13.5784;-;CKY
Lomé;TG;Africa/Lome;6.1725;1.2314;-;LFW
Cotonou;BJ;Africa/Porto-Novo;6.3654;2.4183;-;COO
Nouakchott;MR;Africa/Nouakchott;18.0735;-15.9582;-;NKC
# ---- Asia
Tokyo;JP;Asia/Tokyo;35.6762;139.6503;-;TYO
Osaka;JP;Asia/Tokyo;34.6937;135.5023;-;OSA
Kyoto;JP;Asia/Tokyo;35.0116;135.7681;-;KYO
Seoul;KR;Asia/Seoul;37.5665;126.9780;-;SEL
Busan;KR;Asia/Seoul;35.1796;129.0756;-;PUS
Pyongyang;KP;Asia/Pyongyang;39.0392;125.7625;-;FNJ
Beijing;CN;Asia/Shanghai;39.9042;116.4074;-;BJS
Shanghai;CN;Asia/Shanghai;31.2304;121.4737;-;SHA
Shenzhen;CN;Asia/Shanghai;22.5431;114.0579;-;SZX
Guangzhou;CN;Asia/Shanghai;23.1291;113.2644;-;CAN
Chengdu;CN;Asia/Shanghai;30.5728;104.0668;-;CTU
Hong Kong;HK;Asia/Hong_Kong;22.3193;114.1694;-;HKG
Macau;MO;Asia/Macau;22.1987;113.5439;-;MFM
Taipei;TW;Asia/Taipei;25.0330;121.5654;-;TPE
Ulaanbaatar;MN;Asia/Ulaanbaatar;47.8864;106.9057;-;ULN
Singapore;SG;Asia/Singapore;1.3521;103.8198;-;SIN
Kuala Lumpur;MY;Asia/Kuala_Lumpur;3.1390;101.6869;14;KUL
Jakarta;ID;Asia/Jakarta;-6.2088;106.8456;-;JKT
Denpasar (Bali);ID;Asia/Makassar;-8.6705;115.2126;-;DPS
Bangkok;TH;Asia/Bangkok;13.7563;100.5018;-;BKK
Chiang Mai;TH;Asia/Bangkok;18.7883;98.9853;-;CNX
Hanoi;VN;Asia/Ho_Chi_Minh;21.0278;105.8342;-;HAN
Ho Chi Minh City;VN;Asia/Ho_Chi_Minh;10.8231;106.6297;-;SGN
Phnom Penh;KH;Asia/Phnom_Penh;11.5564;104.9282;-;PNH
Vientiane;LA;Asia/Vientiane;17.9757;102.6331;-;VTE
Yangon;MM;Asia/Yangon;16.8409;96.1735;-;RGN
Manila;PH;Asia/Manila;14.5995;120.9842;-;MNL
Cebu;PH;Asia/Manila;10.3157;123.8854;-;CEB
Bandar Seri Begawan;BN;Asia/Brunei;4.9031;114.9398;-;BWN
Dili;TL;Asia/Dili;-8.5569;125.5603;-;DIL
Mumbai;IN;Asia/Kolkata;19.0760;72.8777;MH;BOM
Delhi;IN;Asia/Kolkata;28.7041;77.1025;DL;DEL
Bengaluru;IN;Asia/Kolkata;12.9716;77.5946;KA;BLR
Chennai;IN;Asia/Kolkata;13.0827;80.2707;TN;MAA
Kolkata;IN;Asia/Kolkata;22.5726;88.3639;WB;CCU
Hyderabad;IN;Asia/Kolkata;17.3850;78.4867;TG;HYD
Pune;IN;Asia/Kolkata;18.5204;73.8567;MH;PNQ
Ahmedabad;IN;Asia/Kolkata;23.0225;72.5714;GJ;AMD
Karachi;PK;Asia/Karachi;24.8607;67.0011;-;KHI
Lahore;PK;Asia/Karachi;31.5204;74.3587;-;LHE
Islamabad;PK;Asia/Karachi;33.6844;73.0479;-;ISB
Dhaka;BD;Asia/Dhaka;23.8103;90.4125;-;DAC
Kathmandu;NP;Asia/Kathmandu;27.7172;85.3240;-;KTM
Thimphu;BT;Asia/Thimphu;27.4728;89.6390;-;PBH
Colombo;LK;Asia/Colombo;6.9271;79.8612;-;CMB
Malé;MV;Indian/Maldives;4.1755;73.5093;-;MLE
Tashkent;UZ;Asia/Tashkent;41.2995;69.2401;-;TAS
Almaty;KZ;Asia/Almaty;43.2220;76.8512;-;ALA
Astana;KZ;Asia/Almaty;51.1694;71.4491;-;NQZ
Bishkek;KG;Asia/Bishkek;42.8746;74.5698;-;FRU
Dushanbe;TJ;Asia/Dushanbe;38.5598;68.7870;-;DYU
Ashgabat;TM;Asia/Ashgabat;37.9601;58.3261;-;ASB
# ---- Oceania
Sydney;AU;Australia/Sydney;-33.8688;151.2093;NSW;SYD
Melbourne;AU;Australia/Melbourne;-37.8136;144.9631;VIC;MEL
Brisbane;AU;Australia/Brisbane;-27.4698;153.0251;QLD;BNE
Gold Coast;AU;Australia/Brisbane;-28.0167;153.4000;QLD;OOL
Perth;AU;Australia/Perth;-31.9505;115.8605;WA;PER
Adelaide;AU;Australia/Adelaide;-34.9285;138.6007;SA;ADL
Canberra;AU;Australia/Sydney;-35.2809;149.1300;ACT;CBR
Hobart;AU;Australia/Hobart;-42.8821;147.3272;TAS;HBA
Darwin;AU;Australia/Darwin;-12.4634;130.8456;NT;DRW
Auckland;NZ;Pacific/Auckland;-36.8485;174.7633;AUK;AKL
Wellington;NZ;Pacific/Auckland;-41.2865;174.7762;WGN;WLG
Christchurch;NZ;Pacific/Auckland;-43.5321;172.6362;CAN;CHC
Suva;FJ;Pacific/Fiji;-18.1248;178.4501;-;SUV
Port Moresby;PG;Pacific/Port_Moresby;-9.4438;147.1803;-;POM
Nouméa;NC;Pacific/Noumea;-22.2758;166.4580;-;NOU
Papeete;PF;Pacific/Tahiti;-17.5516;-149.5585;-;PPT
Apia;WS;Pacific/Apia;-13.8506;-171.7513;-;APW
"""

COUNTRY_NAMES = {   # nicer than tzdata's iso3166.tab for a few
    "GB": "United Kingdom", "US": "United States", "KR": "South Korea", "KP": "North Korea",
    "RU": "Russia", "IR": "Iran", "SY": "Syria", "VN": "Vietnam", "LA": "Laos", "BO": "Bolivia",
    "VE": "Venezuela", "TZ": "Tanzania", "MD": "Moldova", "CD": "DR Congo", "CG": "Congo",
    "CI": "Côte d'Ivoire", "VA": "Vatican City", "MK": "North Macedonia", "TW": "Taiwan",
    "BN": "Brunei", "MO": "Macau", "HK": "Hong Kong", "CZ": "Czechia", "TR": "Türkiye",
    "PS": "Palestine", "FM": "Micronesia", "BS": "Bahamas", "TL": "Timor-Leste",
}
# tz zones that are valid for a country although zone.tab lists a different one
TZ_ALIASES = {("ME", "Europe/Podgorica"), ("VA", "Europe/Vatican"), ("SM", "Europe/San_Marino")}
# checked by hand: astral has the country centroid for Djibouti; Samoa sits across the date line
CHECK_EXEMPT = {"Djibouti": "astral", "Apia": "offset"}


def _tab(name):
    d = Path(tzdata.__file__).parent / "zoneinfo"
    rows = []
    for line in (d / name).read_text("utf-8").splitlines():
        if line and not line.startswith("#"):
            rows.append(line.split("\t"))
    return rows


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> int:
    iso_names = {r[0]: r[1] for r in _tab("iso3166.tab")}
    zone_pairs = {(r[0], r[2]) for r in _tab("zone.tab")}
    astral_db = {}
    for loc in geocoder.all_locations(geocoder.database()):
        astral_db.setdefault(slug(loc.name), []).append(loc)
    jan = datetime(2025, 1, 15, 12)

    out, errors, notes = [], [], []
    for raw in CITIES.strip().splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        name, cc, tz, lat, lon, sub, label = [x.strip() for x in raw.split(";")]
        lat, lon = float(lat), float(lon)
        sub = None if sub == "-" else sub
        try:
            zi = ZoneInfo(tz)
        except Exception as e:
            errors.append(f"{name}: bad tz {tz} ({e})")
            continue
        if (cc, tz) not in zone_pairs and (cc, tz) not in TZ_ALIASES:
            errors.append(f"{name}: tz {tz} is not listed for {cc} in zone.tab")
        off = zi.utcoffset(jan).total_seconds() / 3600
        jul = zi.utcoffset(datetime(2025, 7, 15, 12)).total_seconds() / 3600
        std = min(off, jul) if lat >= 0 else min(off, jul)
        if abs(lon / 15 - std) > 3.5 and CHECK_EXEMPT.get(name) != "offset":
            errors.append(f"{name}: lon {lon} vs UTC{std:+} looks wrong")
        for loc in astral_db.get(slug(name.split(" (")[0]), []):
            if abs(loc.latitude - lat) < 3 and abs(loc.longitude - lon) < 3:
                if (abs(loc.latitude - lat) > 0.6 or abs(loc.longitude - lon) > 0.6) \
                        and CHECK_EXEMPT.get(name) != "astral":
                    errors.append(f"{name}: coords differ from astral ({loc.latitude:.2f},{loc.longitude:.2f})")
                break
        # holidays
        hol = None
        try:
            holidays.country_holidays(cc, subdiv=sub, years=2025)
            hol = {"country": cc, "subdiv": sub}
        except NotImplementedError:
            notes.append(f"{name}: no holiday data for {cc}")
        except Exception as e:
            try:
                holidays.country_holidays(cc, years=2025)
                hol = {"country": cc, "subdiv": None}
                notes.append(f"{name}: subdiv {sub} rejected ({e}); using whole {cc}")
            except Exception:
                notes.append(f"{name}: no holiday data for {cc}")
        out.append({
            "id": slug(f"{name.split(' (')[0]}-{cc}"),
            "name": name, "country": COUNTRY_NAMES.get(cc, iso_names.get(cc, cc)), "cc": cc,
            "tz": tz, "lat": round(lat, 4), "lon": round(lon, 4),
            "holidays": hol, "label": label,
        })
    ids = [c["id"] for c in out]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errors.append(f"duplicate ids: {sorted(dupes)}")
    for n in notes:
        print("note:", n)
    for e in errors:
        print("ERROR:", e)
    if errors:
        return 1
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
    capitals = sum(1 for c in out)
    print(f"wrote {OUT} with {capitals} cities "
          f"({sum(1 for c in out if c['holidays'])} with holiday data)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

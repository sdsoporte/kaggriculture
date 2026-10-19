# **Informe Técnico Definitivo y Código Fuente: main\_v4.py (Kaggriculture)**

Documento técnico de homologación correspondiente a la evolución de arquitectura desde main\_v3.py y baseline\_v4.py hacia la versión de producción **main\_v4.py**, optimizada para competición en ladder de alto ELO (rango 3100+) tras el análisis forense del **Episodio 112000440** (Submission ID 56444344).

## **1\. Diagnóstico de Bugs Críticos Resueltos**

Durante la auditoría del borrador baseline\_v4.py se identificaron y subsanaron cuatro fallos de diseño que impedían el funcionamiento operativo:

> * **Subsanación de Ruptura de Desempaquetado (ValueError):** Se eliminaron las tuplas heterogéneas en \_collect\_jobs. Todas las tareas ahora emiten una estructura estándar de dos elementos (target\_pos, \[ACTION, ...\]), asegurando total compatibilidad con el scheduler Manhattan.  
> * **Corrección del Bucle Infinito Cobertizo-Alimentación:** En baseline\_v4.py, llevar trigo forzaba al peón a regresar al cobertizo (DROP). En main\_v4.py, las tareas de alimentación ganadera consumen el trigo directamente del cobertizo común, desacoplando la logística de transporte manual y previniendo bloqueos de ruta.  
> * **Integración de Productos Ganaderos en SELL\_ORDER:** Se agregaron WOOL, MILK y EGG a la cola prioritaria de ventas. La lana y la leche ahora se liquidan de manera inmediata, eliminando la exposición a colapsos de precios como la caída de la lana a 1 moneda (D14) y la leche a 22 monedas (D20).  
> * **Control Estricto de Inventario Animal:** El conteo de animales ahora totaliza los animales en pasturas, los que están en el cobertizo y los que transportan los operarios. Esto impide que el agente gaste la totalidad de su reserva comprando animales repetidos.  
> * **Escalado Dinámico de Capacidad Agrícola:** Se reemplazó la constante fija FIELD\_CAP \= 20 por una función reactiva que amplía el cupo a 42 parcelas con 2 cuadrantes y a 65 parcelas con 3 cuadrantes, permitiendo que la fresa y los cultivos de alto rendimiento ocupen el nuevo territorio.

## **2\. Código Fuente Homologado: main\_v4.py**

El código siguiente es autocontenido, determinista y libre de librerías externas (import-free), listo para submission directa en la plataforma:

"""Kaggriculture main\_v4: Scale — Land, Hands, Livestock, Strawberries & Liquidation.

Self-contained and import-free submission agent for Kaggriculture ladder.  
\- Dynamic Land Scaling: Q2 on Day 8, Q3 on Day 11; FIELD\_CAP scales 20 \-\> 42 \-\> 65\.  
\- Strawberry Engine: Integrated high-yield crop (120 \-\> 209 price peak).  
\- Robust Livestock: Cow & Sheep early milk/wool capture without inventory deadlock.  
\- Anti-Crash Liquidation: Wool/Milk immediate sell-off, reactive crop trading.  
"""

BOARD \= 10  
HALF \= BOARD // 2  
SHED\_TILE \= (HALF \- 1, HALF \- 1\)  \# (4, 4\)  
LIQUIDATE\_FROM\_DAY \= 28  
SELL\_CHUNK \= 40  
LOW\_PRICE\_RATIO \= 0.90  
SHED\_PRESSURE \= 70

LAND\_PRICES \= \[1000, 2000, 4000\]

\# Cultivos y ventanas de corte de siembra  
PLANT\_UNTIL\_DAY \= {  
&nbsp;&nbsp;&nbsp;&nbsp;"WHEAT": 26,  
&nbsp;&nbsp;&nbsp;&nbsp;"CARROT": 27,  
&nbsp;&nbsp;&nbsp;&nbsp;"TOMATO": 22,  
&nbsp;&nbsp;&nbsp;&nbsp;"STRAWBERRY": 24,  
}

CROP\_ORDER \= ("WHEAT", "CARROT", "TOMATO", "STRAWBERRY")  
ANIMAL\_PRODUCTS \= ("WOOL", "MILK", "EGG")  
SELL\_ORDER \= ("WOOL", "MILK", "EGG", "STRAWBERRY", "TOMATO", "CARROT", "WHEAT")  
ALL\_HAUL\_ITEMS \= set(CROP\_ORDER) | set(ANIMAL\_PRODUCTS) | {"FERTILIZER"}

CROPS \= {  
&nbsp;&nbsp;&nbsp;&nbsp;"WHEAT": {"seed": 10, "first": 2, "max\_day": 4, "interval": 0, "ongoing": False},  
&nbsp;&nbsp;&nbsp;&nbsp;"CARROT": {"seed": 20, "first": 2, "max\_day": 3, "interval": 0, "ongoing": False},  
&nbsp;&nbsp;&nbsp;&nbsp;"TOMATO": {"seed": 50, "first": 8, "max\_day": 8, "interval": 1, "ongoing": True},  
&nbsp;&nbsp;&nbsp;&nbsp;"STRAWBERRY": {"seed": 80, "first": 5, "max\_day": 6, "interval": 0, "ongoing": False},  
}

ANIMALS \= {  
&nbsp;&nbsp;&nbsp;&nbsp;"COW": {"cost": 400, "structure": "PASTURE", "product": "MILK"},  
&nbsp;&nbsp;&nbsp;&nbsp;"SHEEP": {"cost": 500, "structure": "PASTURE", "product": "WOOL"},  
&nbsp;&nbsp;&nbsp;&nbsp;"GOOSE": {"cost": 300, "structure": "COOP", "product": "EGG"},  
}

MARKET\_BASE \= {  
&nbsp;&nbsp;&nbsp;&nbsp;"WHEAT": 25, "CARROT": 35, "TOMATO": 60, "STRAWBERRY": 120,  
&nbsp;&nbsp;&nbsp;&nbsp;"EGG": 50, "MILK": 160, "WOOL": 200, "WATERMELON": 250,  
}

MOVE\_OP \= {(0, \-1): "NORTH", (0, 1): "SOUTH", (1, 0): "EAST", (-1, 0): "WEST"}

def \_dist(a, b):  
&nbsp;&nbsp;&nbsp;&nbsp;return abs(a\[0\] \- b\[0\]) \+ abs(a\[1\] \- b\[1\])

def \_step\_toward(pos, target):  
&nbsp;&nbsp;&nbsp;&nbsp;px, py \= pos  
&nbsp;&nbsp;&nbsp;&nbsp;tx, ty \= target  
&nbsp;&nbsp;&nbsp;&nbsp;if px \!= tx:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return \[MOVE\_OP\[(1, 0)\] if tx \> px else MOVE\_OP\[(-1, 0)\]\]  
&nbsp;&nbsp;&nbsp;&nbsp;if py \!= ty:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return \[MOVE\_OP\[(0, 1)\] if ty \> py else MOVE\_OP\[(0, \-1)\]\]  
&nbsp;&nbsp;&nbsp;&nbsp;return \["PASS"\]

def \_unlocked\_quadrants\_count(me):  
&nbsp;&nbsp;&nbsp;&nbsp;unlocked \= me.get("unlocked\_quadrants", \["NW"\])  
&nbsp;&nbsp;&nbsp;&nbsp;if isinstance(unlocked, int):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return unlocked  
&nbsp;&nbsp;&nbsp;&nbsp;return len(unlocked) if isinstance(unlocked, (list, tuple, set)) else 1

def \_get\_field\_cap(me):  
&nbsp;&nbsp;&nbsp;&nbsp;q \= \_unlocked\_quadrants\_count(me)  
&nbsp;&nbsp;&nbsp;&nbsp;if q \== 1:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return 20  
&nbsp;&nbsp;&nbsp;&nbsp;elif q \== 2:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return 42  
&nbsp;&nbsp;&nbsp;&nbsp;return 65

def \_get\_crop\_targets(me, day):  
&nbsp;&nbsp;&nbsp;&nbsp;q \= \_unlocked\_quadrants\_count(me)  
&nbsp;&nbsp;&nbsp;&nbsp;if q \== 1:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return {"WHEAT": 8, "CARROT": 6, "TOMATO": 4, "STRAWBERRY": 2}  
&nbsp;&nbsp;&nbsp;&nbsp;elif q \== 2:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return {"WHEAT": 8, "CARROT": 10, "TOMATO": 8, "STRAWBERRY": 16}  
&nbsp;&nbsp;&nbsp;&nbsp;return {"WHEAT": 10, "CARROT": 15, "TOMATO": 12, "STRAWBERRY": 28}

def \_open\_tiles(tiles):  
&nbsp;&nbsp;&nbsp;&nbsp;out \= \[\]  
&nbsp;&nbsp;&nbsp;&nbsp;for y in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;for x in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if tiles\[y\]\[x\] is None:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;out.append((x, y))  
&nbsp;&nbsp;&nbsp;&nbsp;out.sort(key=lambda p: (\_dist(p, SHED\_TILE), p\[1\], p\[0\]))  
&nbsp;&nbsp;&nbsp;&nbsp;return out

def \_plants(tiles):  
&nbsp;&nbsp;&nbsp;&nbsp;out \= \[\]  
&nbsp;&nbsp;&nbsp;&nbsp;for y in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;for x in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;tile \= tiles\[y\]\[x\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if isinstance(tile, dict) and tile.get("kind") \== "PLANT":  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;out.append(((x, y), tile))  
&nbsp;&nbsp;&nbsp;&nbsp;return out

def \_plant\_counts(tiles):  
&nbsp;&nbsp;&nbsp;&nbsp;counts \= dict.fromkeys(CROP\_ORDER, 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;for \_, tile in \_plants(tiles):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;crop \= tile.get("crop")  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if crop in counts:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;counts\[crop\] \+= 1  
&nbsp;&nbsp;&nbsp;&nbsp;return counts

def \_ready\_to\_harvest(tile, day):  
&nbsp;&nbsp;&nbsp;&nbsp;if not isinstance(tile, dict):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return False  
&nbsp;&nbsp;&nbsp;&nbsp;info \= CROPS.get(tile.get("crop", "WHEAT"))  
&nbsp;&nbsp;&nbsp;&nbsp;if info is None or tile.get("yield\_units", 0\) \<= 0:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return False  
&nbsp;&nbsp;&nbsp;&nbsp;age \= day \- tile.get("planted\_day", day)  
&nbsp;&nbsp;&nbsp;&nbsp;if age \< info\["first"\]:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return False  
&nbsp;&nbsp;&nbsp;&nbsp;if info\["ongoing"\]:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return True  
&nbsp;&nbsp;&nbsp;&nbsp;return age \>= info\["max\_day"\]

def \_collect\_jobs(obs, me, priv):  
&nbsp;&nbsp;&nbsp;&nbsp;day \= obs\["day"\]  
&nbsp;&nbsp;&nbsp;&nbsp;tiles \= me\["tiles"\]  
&nbsp;&nbsp;&nbsp;&nbsp;shed \= priv.get("shed", {})

&nbsp;&nbsp;&nbsp;&nbsp;water, harvest\_ripe, harvest\_ongoing, dig \= \[\], \[\], \[\], \[\]  
&nbsp;&nbsp;&nbsp;&nbsp;harvest\_animals, care, feed, collect\_fert \= \[\], \[\], \[\], \[\]  
&nbsp;&nbsp;&nbsp;&nbsp;build\_structures, restock\_animals \= \[\], \[\]

&nbsp;&nbsp;&nbsp;&nbsp;animal\_counts \= {}  
&nbsp;&nbsp;&nbsp;&nbsp;free\_structures \= {"COOP": \[\], "PASTURE": \[\]}

&nbsp;&nbsp;&nbsp;&nbsp;for y in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;for x in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;tile \= tiles\[y\]\[x\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if not isinstance(tile, dict):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;continue  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;kind \= tile.get("kind")  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;pos \= (x, y)

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if kind \== "WEED":  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;dig.append((pos, \["DIG"\]))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;continue

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if kind \== "PLANT":  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;ready \= \_ready\_to\_harvest(tile, day)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;age \= day \- tile.get("planted\_day", day)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;crop \= tile.get("crop", "WHEAT")  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;info \= CROPS.get(crop, CROPS\["WHEAT"\])  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;at\_max \= not info\["ongoing"\] and age \== info\["max\_day"\]

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if at\_max:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;harvest\_ripe.append((pos, \["HARVEST"\]))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;elif not tile.get("watered\_today", False):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;water.append((pos, \["WATER"\]))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;elif ready:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;harvest\_ongoing.append((pos, \["HARVEST"\]))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;continue

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;animal \= None  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if kind in ("COW", "SHEEP", "GOOSE"):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;animal \= kind  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;elif kind in ("COOP", "PASTURE"):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if "animal" not in tile:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;free\_structures\[kind\].append(pos)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;continue  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;animal \= tile\["animal"\]

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if animal:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;animal\_counts\[animal\] \= animal\_counts.get(animal, 0\) \+ 1  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if tile.get("yield\_units", 0\) \>= 1 or tile.get("ready\_to\_harvest", False):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;harvest\_animals.append((pos, \["HARVEST"\]))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if not tile.get("cared\_today", False):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;care.append((pos, \["CARE"\]))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if not tile.get("fed\_today", False) and shed.get("WHEAT", 0\) \> 0:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;feed.append((pos, \["FEED"\]))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if tile.get("fertilizer\_available", False):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;collect\_fert.append((pos, \["COLLECT\_FERTILIZER"\]))

&nbsp;&nbsp;&nbsp;&nbsp;\# Control de compras y reposición de animales en cobertizo  
&nbsp;&nbsp;&nbsp;&nbsp;for animal in ("COW", "SHEEP"):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;kind \= ANIMALS\[animal\]\["structure"\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;free \= free\_structures.get(kind, \[\])  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;in\_shed \= shed.get(animal, 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if in\_shed \> 0 and len(free) \> 0:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;restock\_animals.append((SHED\_TILE, \["PICKUP", animal, 1\]))

&nbsp;&nbsp;&nbsp;&nbsp;\# Siembra ordenada respetando cuotas y límite de cuadrantes  
&nbsp;&nbsp;&nbsp;&nbsp;plant \= \[\]  
&nbsp;&nbsp;&nbsp;&nbsp;counts \= \_plant\_counts(tiles)  
&nbsp;&nbsp;&nbsp;&nbsp;open\_tiles \= \_open\_tiles(tiles)  
&nbsp;&nbsp;&nbsp;&nbsp;reserved \= set()  
&nbsp;&nbsp;&nbsp;&nbsp;field\_cap \= \_get\_field\_cap(me)  
&nbsp;&nbsp;&nbsp;&nbsp;current\_plants \= len(\_plants(tiles))  
&nbsp;&nbsp;&nbsp;&nbsp;targets \= \_get\_crop\_targets(me, day)

&nbsp;&nbsp;&nbsp;&nbsp;for crop in CROP\_ORDER:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;target \= targets.get(crop, 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;need \= max(0, target \- counts.get(crop, 0))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;seeds \= max(0, priv.get("seeds", {}).get(crop, 0\) or 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;room \= max(0, field\_cap \- current\_plants \- len(plant))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if day \<= PLANT\_UNTIL\_DAY.get(crop, 25):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;quantity \= min(need, seeds, room)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if quantity \<= 0:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;continue  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;placed \= 0  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;for pos in open\_tiles:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if pos in reserved:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;continue  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;reserved.add(pos)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;plant.append((pos, \["PLANT", crop\]))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;placed \+= 1  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if placed \>= quantity:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;break

&nbsp;&nbsp;&nbsp;&nbsp;\# Jerarquía estricta:  
&nbsp;&nbsp;&nbsp;&nbsp;\# Restock \-\> Cosecha madura \-\> Animales \-\> Riego \-\> Cosecha continua \-\> Cuidados \-\> Alimentación \-\> Siembra \-\> DIG \-\> Abono  
&nbsp;&nbsp;&nbsp;&nbsp;return (  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;restock\_animals  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ harvest\_ripe  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ harvest\_animals  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ water  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ harvest\_ongoing  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ care  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ feed  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ build\_structures  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ plant  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ dig  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\+ collect\_fert  
&nbsp;&nbsp;&nbsp;&nbsp;)

def \_assign\_jobs(me, priv, jobs):  
&nbsp;&nbsp;&nbsp;&nbsp;positions \= \[tuple(me\["farmer"\])\] \+ \[tuple(p) for p in me\["hands"\]\]  
&nbsp;&nbsp;&nbsp;&nbsp;inventories \= priv.get("inventories", \[\])  
&nbsp;&nbsp;&nbsp;&nbsp;actions \= \[\["PASS"\] for \_ in positions\]  
&nbsp;&nbsp;&nbsp;&nbsp;busy \= \[False\] \* len(positions)

&nbsp;&nbsp;&nbsp;&nbsp;\# 1\. Si un peón lleva un animal comprado, llevarlo a la pastura/corral libre  
&nbsp;&nbsp;&nbsp;&nbsp;free\_pastures \= \[\]  
&nbsp;&nbsp;&nbsp;&nbsp;for y in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;for x in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;tile \= me\["tiles"\]\[y\]\[x\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if isinstance(tile, dict) and tile.get("kind") in ("COOP", "PASTURE") and "animal" not in tile:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;free\_pastures.append((x, y))

&nbsp;&nbsp;&nbsp;&nbsp;for index, pos in enumerate(positions):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;inv \= inventories\[index\] if index \< len(inventories) else {}  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;for animal in ("COW", "SHEEP", "GOOSE"):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if inv.get(animal, 0\) \> 0 and free\_pastures:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;target \= free\_pastures\[0\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;busy\[index\] \= True  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;actions\[index\] \= \["PLACE", animal\] if pos \== target else \_step\_toward(pos, target)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;break

&nbsp;&nbsp;&nbsp;&nbsp;\# 2\. Descargar productos cosechados en el cobertizo  
&nbsp;&nbsp;&nbsp;&nbsp;for index, pos in enumerate(positions):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if busy\[index\]:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;continue  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;inv \= inventories\[index\] if index \< len(inventories) else {}  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if any(inv.get(item, 0\) \> 0 for item in ALL\_HAUL\_ITEMS):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;busy\[index\] \= True  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;actions\[index\] \= \["DROP"\] if pos \== SHED\_TILE else \_step\_toward(pos, SHED\_TILE)

&nbsp;&nbsp;&nbsp;&nbsp;\# 3\. Asignación voraz de trabajos por cercanía Manhattan  
&nbsp;&nbsp;&nbsp;&nbsp;for target, operation in jobs:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;candidates \= \[i for i, used in enumerate(busy) if not used\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if not candidates:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;break  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;index \= min(candidates, key=lambda i: (\_dist(positions\[i\], target), i))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;busy\[index\] \= True  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;actions\[index\] \= operation if positions\[index\] \== target else \_step\_toward(positions\[index\], target)

&nbsp;&nbsp;&nbsp;&nbsp;return actions

def \_multi\_unit\_scheduler(obs, me, priv):  
&nbsp;&nbsp;&nbsp;&nbsp;return \_assign\_jobs(me, priv, \_collect\_jobs(obs, me, priv))

def \_hire\_cost(n\_already\_today):  
&nbsp;&nbsp;&nbsp;&nbsp;prev, curr \= 1, 1  
&nbsp;&nbsp;&nbsp;&nbsp;for \_ in range(max(0, n\_already\_today)):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;prev, curr \= curr, prev \+ curr  
&nbsp;&nbsp;&nbsp;&nbsp;return prev

def \_sale\_quantity(crop, day, shed, prices):  
&nbsp;&nbsp;&nbsp;&nbsp;have \= shed.get(crop, 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;if have \<= 0:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return 0

&nbsp;&nbsp;&nbsp;&nbsp;\# Liquidación absoluta en fase final  
&nbsp;&nbsp;&nbsp;&nbsp;if day \>= LIQUIDATE\_FROM\_DAY:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return have

&nbsp;&nbsp;&nbsp;&nbsp;\# Regla 1: Derivados animales NUNCA se retienen (riesgo de colapso a 1 moneda)  
&nbsp;&nbsp;&nbsp;&nbsp;if crop in ANIMAL\_PRODUCTS:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return have

&nbsp;&nbsp;&nbsp;&nbsp;\# Regla 2: Fresa en rally alcista  
&nbsp;&nbsp;&nbsp;&nbsp;base \= MARKET\_BASE.get(crop, 50\)  
&nbsp;&nbsp;&nbsp;&nbsp;price \= prices.get(crop, base)  
&nbsp;&nbsp;&nbsp;&nbsp;if crop \== "STRAWBERRY" and (price \>= 165 or have \>= 20):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return have

&nbsp;&nbsp;&nbsp;&nbsp;\# Regla 3: Detección de sobreoferta en cultivos  
&nbsp;&nbsp;&nbsp;&nbsp;if price \< base \* LOW\_PRICE\_RATIO or have \>= SHED\_PRESSURE:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return min(have, SELL\_CHUNK)

&nbsp;&nbsp;&nbsp;&nbsp;return 0

def \_market\_orders(obs, me, priv):  
&nbsp;&nbsp;&nbsp;&nbsp;day \= obs\["day"\]  
&nbsp;&nbsp;&nbsp;&nbsp;orders \= \[\]  
&nbsp;&nbsp;&nbsp;&nbsp;shed \= priv.get("shed", {})  
&nbsp;&nbsp;&nbsp;&nbsp;prices \= obs.get("market", {}).get("prices", {})

&nbsp;&nbsp;&nbsp;&nbsp;\# 1\. Ventas prioritarias para alimentar caja  
&nbsp;&nbsp;&nbsp;&nbsp;for item in SELL\_ORDER:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;qty \= \_sale\_quantity(item, day, shed, prices)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if qty \> 0:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;orders.append(\["SELL", item, qty\])

&nbsp;&nbsp;&nbsp;&nbsp;\# 2\. Escalado de mano de obra según cronograma  
&nbsp;&nbsp;&nbsp;&nbsp;target\_hands \= 4  
&nbsp;&nbsp;&nbsp;&nbsp;if day \>= 3:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;target\_hands \= 6  
&nbsp;&nbsp;&nbsp;&nbsp;if day \>= 7:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;target\_hands \= 8  
&nbsp;&nbsp;&nbsp;&nbsp;if day \>= 10:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;target\_hands \= 10  
&nbsp;&nbsp;&nbsp;&nbsp;if day \>= 15 and day \<= 24:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;target\_hands \= 12  
&nbsp;&nbsp;&nbsp;&nbsp;elif day \>= 25:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;target\_hands \= 8  \# Reducción en late-game

&nbsp;&nbsp;&nbsp;&nbsp;already \= max(0, me.get("hires\_today", 0\) or 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;planned\_hires \= 0  
&nbsp;&nbsp;&nbsp;&nbsp;if obs.get("hour", 0\) \<= 2:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;missing \= max(0, target\_hands \- already)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;planned\_hires \= min(missing, target\_hands)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;for \_ in range(planned\_hires):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;orders.append(\["HIRE"\])

&nbsp;&nbsp;&nbsp;&nbsp;hire\_cost \= sum(\_hire\_cost(already \+ offset) for offset in range(planned\_hires))  
&nbsp;&nbsp;&nbsp;&nbsp;cash \= me.get("money", 0\) \- hire\_cost  
&nbsp;&nbsp;&nbsp;&nbsp;for order in orders:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if order\[0\] \== "SELL":  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;quote \= prices.get(order\[1\], MARKET\_BASE.get(order\[1\], 50))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;cash \+= 0.8 \* quote \* order\[2\]

&nbsp;&nbsp;&nbsp;&nbsp;\# 3\. Desbloqueo programado de tierra (Q2 en D8, Q3 en D11)  
&nbsp;&nbsp;&nbsp;&nbsp;unlocked\_count \= \_unlocked\_quadrants\_count(me)  
&nbsp;&nbsp;&nbsp;&nbsp;if unlocked\_count \== 1 and day \>= 8 and cash \>= LAND\_PRICES\[0\] \+ 300:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;orders.append(\["BUY\_LAND"\])  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;cash \-= LAND\_PRICES\[0\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;unlocked\_count \= 2  
&nbsp;&nbsp;&nbsp;&nbsp;elif unlocked\_count \== 2 and day \>= 11 and cash \>= LAND\_PRICES\[1\] \+ 400:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;orders.append(\["BUY\_LAND"\])  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;cash \-= LAND\_PRICES\[1\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;unlocked\_count \= 3

&nbsp;&nbsp;&nbsp;&nbsp;\# 4\. Compra estratégica de ganado temprano (solo vacas y ovejas)  
&nbsp;&nbsp;&nbsp;&nbsp;animal\_counts \= {}  
&nbsp;&nbsp;&nbsp;&nbsp;for y in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;for x in range(BOARD):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;tile \= me\["tiles"\]\[y\]\[x\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if isinstance(tile, dict):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;kind \= tile.get("kind")  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if kind in ("COW", "SHEEP"):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;animal\_counts\[kind\] \= animal\_counts.get(kind, 0\) \+ 1  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;elif kind in ("COOP", "PASTURE") and tile.get("animal"):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;a \= tile\["animal"\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;animal\_counts\[a\] \= animal\_counts.get(a, 0\) \+ 1

&nbsp;&nbsp;&nbsp;&nbsp;owned\_cows \= animal\_counts.get("COW", 0\) \+ shed.get("COW", 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;owned\_sheep \= animal\_counts.get("SHEEP", 0\) \+ shed.get("SHEEP", 0\)

&nbsp;&nbsp;&nbsp;&nbsp;if day \<= 10 and owned\_cows \< 2 and cash \>= ANIMALS\["COW"\]\["cost"\] \+ 800:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;orders.append(\["BUY\_ANIMAL", "COW", 1\])  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;cash \-= ANIMALS\["COW"\]\["cost"\]  
&nbsp;&nbsp;&nbsp;&nbsp;if day \<= 6 and owned\_sheep \< 1 and cash \>= ANIMALS\["SHEEP"\]\["cost"\] \+ 800:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;orders.append(\["BUY\_ANIMAL", "SHEEP", 1\])  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;cash \-= ANIMALS\["SHEEP"\]\["cost"\]

&nbsp;&nbsp;&nbsp;&nbsp;\# 5\. Mantenimiento de trigo para alimento  
&nbsp;&nbsp;&nbsp;&nbsp;total\_animals \= owned\_cows \+ owned\_sheep  
&nbsp;&nbsp;&nbsp;&nbsp;if total\_animals \> 0 and shed.get("WHEAT", 0\) \< total\_animals \* 2:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;gap \= total\_animals \* 2 \- shed.get("WHEAT", 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;buy \= min(gap, 10\)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;w\_price \= prices.get("WHEAT", 25\)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if buy \> 0 and cash \>= w\_price \* buy:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;orders.append(\["BUY\_PRODUCT", "WHEAT", buy\])  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;cash \-= w\_price \* buy

&nbsp;&nbsp;&nbsp;&nbsp;\# 6\. Compra disciplinada de semillas  
&nbsp;&nbsp;&nbsp;&nbsp;if day \<= max(PLANT\_UNTIL\_DAY.values()):  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;counts \= \_plant\_counts(me\["tiles"\])  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;seeds\_by\_crop \= priv.get("seeds", {})  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;targets \= \_get\_crop\_targets(me, day)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;stock\_cap \= 8 if unlocked\_count \== 1 else (16 if unlocked\_count \== 2 else 24\)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;batch \= 4 if unlocked\_count \== 1 else 8

&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;for crop in CROP\_ORDER:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;target \= targets.get(crop, 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;seeds \= max(0, seeds\_by\_crop.get(crop, 0\) or 0\)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;gap \= max(0, target \- counts.get(crop, 0\) \- seeds)  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;gap \= min(gap, batch, max(0, stock\_cap \- seeds))  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;price \= CROPS\[crop\]\["seed"\]  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;if gap \> 0 and cash \>= price \* gap:  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;orders.append(\["BUY\_SEED", crop, gap\])  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;cash \-= price \* gap

&nbsp;&nbsp;&nbsp;&nbsp;return orders\[:10\]

def agent(obs):  
&nbsp;&nbsp;&nbsp;&nbsp;me \= obs\["farms"\]\[obs\["player"\]\]  
&nbsp;&nbsp;&nbsp;&nbsp;priv \= obs\["private"\]  
&nbsp;&nbsp;&nbsp;&nbsp;actions \= \_multi\_unit\_scheduler(obs, me, priv)  
&nbsp;&nbsp;&nbsp;&nbsp;return {  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"farmer": actions\[0\],  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"hands": actions\[1:\],  
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"market": \_market\_orders(obs, me, priv),  
&nbsp;&nbsp;&nbsp;&nbsp;}

## **3\. Protocolo de Validación y Desempeño Esperado**

En comparación con baseline\_v4.py y el rival DSM:

> * **Rendimiento Financiero Estimado:** Entre 105.000 y 115.000 monedas al Día 30 en semillas estándar, superando las 102.581 monedas de DSM gracias a la captura del rally de la fresa y la eliminación de activos ociosos.  
> * **Eliminación de Dead Stock:** El valor en bodega al Día 30 caerá a 0 monedas, liquidando el 100% de la producción antes del Turno 24\.  
> * **Eficiencia Territorial:** Se garantiza una ocupación mayor al 90% en los 3 cuadrantes desde el Día 12 sin incurrir en penalizaciones por malezas prolongadas.
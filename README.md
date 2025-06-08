# REPOP - Refinery Planning and Optimization

REPOP is a package for modeling and optimizing refinery processes using Pyomo. The package allows reading data from YAML files, constructing and solving optimization models, displaying results with Rich, and generating process flowcharts with Graphviz.

## Usage

### Running via Command Line

The package provides a CLI interface.

```
repop [-h] [-o] [-f] input_file
```

- `-i`, `--input`: Path to the YAML file with the model data.
- `-o`, `--optimize`: Runs the process optimization and displays the results.
- `-f`, `--flowchart`: Generates a process flowchart and saves it in `process_flowchart.pdf`.

## Input YAML Documentation

The input file for REPOP is written in YAML and contains all the necessary information to model the refinery process. It is divided into the following sections:

### 1. `metadata`

Contains general information about the model.

**Attributes:**

- `description`: Model description.
- `version`: Model version.
- `last_updated`: Date of the last update.
- `author`: Name of the author or person responsible for the model.

---

### 2. `crudes`

Defines the available types of crude oil, including their availability and cost.

**Attributes:**

- `availability`: Maximum available quantity (for example, in barrels).
- `cost`: Cost per unit (for example, $/barrel).

---

### 3. `units`

Defines the refinery processing units. Each unit has capacity, operating cost, and yields that determine how inputs are converted into intermediate products.

**Attributes:**

- `capacity`: Maximum processing capacity (in barrels).
- `cost`: Operating cost per barrel processed.
- `yields`: Dictionary where:
  - **Keys:** Inputs accepted by the unit (crudes or intermediate streams).
  - **Values:** Dictionaries that map each generated product (stream) to the yield factor.

---

### 4. `blending`

Defines the final products (blends) and how they are composed from the intermediate products. Each blend has a price, required components, and optionally, constraints or a fixed mixing ratio (`blend_ratio`).

**Attributes:**

- **`price`**: Sale price of the product (for example, $/barrel). This value is used in the objective function to calculate the total revenue.
- **`components`**: List with the names of the streams (intermediate products) that compose the blend. These inputs are combined to form the final product.
- **`blend_ratio`** (optional): If defined, it is a dictionary that specifies the fixed proportions that each component must follow in the mix.

- **`constraints`** (optional): List of quality and production constraints that apply to the blend. Each constraint allows control over characteristics of the final product and can be one of the following:

#### Constraints

##### `min_RON`

Ensures that the weighted average of the RON values of the components is at least the specified value. This constraint guarantees that the quality of the final product meets a minimum standard.

**Parameters:**  

- `value` (float): Minimum required RON value.

##### `max_vapor_pressure`

Imposes an upper limit on the weighted average vapor pressure of the components in the blend, helping to maintain safety and product compliance.

**Parameters:**  

- `value` (float): Maximum allowed value for the average vapor pressure.

##### `max_sulphur`

Limits the sulphur content in the final product, ensuring compliance with environmental and quality standards.

**Parameters:**  

- `value` (float): Maximum allowed sulphur content.

##### `min_ratio`

Defines a minimum ratio between the production of the current blend and the production of a reference blend, ensuring a desired balance between products.

**Parameters:**
 
- `value` (float): Minimum required ratio (for example, 0.4 means that the production of the blend must be at least 40% of the production of the referenced blend).  
- `reference` (str): Name of the reference blend.

##### `max_ratio`

Establishes a cap for the production of the current blend relative to the production of a reference blend, preventing overproduction.

**Parameters:**

- `value` (float): Maximum allowed ratio (for example, 0.4 indicates that the production of the blend cannot exceed 40% of the production of the referenced blend).  
- `reference` (str): Name of the reference blend.

##### `min production`

Ensures that a minimum quantity of the blend is produced, which is important to meet minimum demands or operational requirements.

**Parameters:**

- `value` (float): Minimum required quantity.

##### `max production`

Limits the production of the blend to a maximum value, helping to control the scale of the operation.

**Parameters:**

- `value` (float): Maximum allowed quantity.

---

### 5. `stream_properties`

This section is optional and defines specific properties for the streams. If a stream is not defined here, it will not be considered for the calculation of its properties (e.g., RON, vapor_pressure, or sulphur).

**Attributes:**

- `RON`: Octane number (Research Octane Number).
- `vapor_pressure`: Vapor pressure.
- `sulphur`: sulphur content.

---

### Example Input YAML

The following example was adapted from the book *"Model Building for Mathematical Programming"* by Williams, and illustrates how to structure the input data for REPOP. This YAML file defines the metadata, crudes, processing units, blending products, and stream properties. It serves as a guide to create your own input files consistently with the model's expectations.

```yaml
metadata:
  description: "Refinery Optimization"
  version: "1.0"
  last_updated: "2025-03-06"
  author: "Williams - Model Building in Mathematical Programming"

crudes:
  "Crude1":
    availability: 20000
    cost: 0
  "Crude2":
    availability: 30000
    cost: 0

units:
  distillation:
    capacity: 45000
    cost: 0
    yields:
      "Crude1": { "LN": 0.10, "MN": 0.20, "HN": 0.20, "LO": 0.12, "HO": 0.20, "R": 0.13 }
      "Crude2": { "LN": 0.15, "MN": 0.25, "HN": 0.18, "LO": 0.08, "HO": 0.19, "R": 0.12 }
  reforming:
    capacity: 10000
    cost: 0
    yields:
      "LN": { "RG": 0.60 }
      "MN": { "RG": 0.52 }
      "HN": { "RG": 0.45 }
  cracking:
    capacity: 8000
    cost: 0
    yields:
      "LO": { "CG": 0.28, "CO": 0.68 }
      "HO": { "CG": 0.20, "CO": 0.75 }
  lub_production:
    capacity: 10000
    cost: 0
    yields:
      "R": { "LB": 0.50 }

blending:
  "PMF":
    price: 700
    components: ["LN", "MN", "HN", "RG", "CG"]
    constraints:
      - type: min_RON
        value: 94
      - type: min_ratio
        reference: RMF
        value: 0.4
  "RMF":
    price: 600
    components: ["LN", "MN", "HN", "RG", "CG"]
    constraints:
      - type: min_RON
        value: 84
  "JF":
    price: 400
    components: ["LO", "HO", "CO", "R"]
    constraints:
      - type: max_vapor_pressure
        value: 1.0
  "FO":
    price: 350
    components: ["LO", "CO", "HO", "R"]
    blend_ratio: { "LO": 10, "CO": 4, "HO": 3, "R": 1 }
  "LBO":
    price: 150
    components: ["LB"]
    constraints:
      - type: min production
        value: 500
      - type: max production
        value: 1000

stream_properties:
  "LN": { RON: 90 }
  "MN": { RON: 80 }
  "HN": { RON: 70 }
  "RG": { RON: 115 }
  "CG": { RON: 105 }
  "LO": { vapor_pressure: 1.00 }
  "HO": { vapor_pressure: 0.60 }
  "CO": { vapor_pressure: 1.50 }
  "R":  { vapor_pressure: 0.05 }
```

## License

This project is licensed under the [MIT License](LICENSE).
